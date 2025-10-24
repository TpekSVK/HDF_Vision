# app/services/recipe_service.py
from pathlib import Path
import json
from typing import Iterable, List, Optional, Sequence

from app.models.schema import RecipeData, RecipeV2, Tool, RecipeStep
from app.services.db_service import DbService
from app.services.tool_service import ToolService, DEFAULT_THRESHOLDS
from app.services.storage_service import load_recipe_config, save_recipe_config

class RecipeService:
    def __init__(self, base_dir="/data", db: DbService | None = None):
        self.base = Path(base_dir)
        self.db = db or DbService(self.base / "HDF_Vision.db")
        self.tool = ToolService(base_dir=base_dir)
        self._draft_tools: dict[str, List[Tool]] = {}
        self._locator_autosort: dict[str, bool] = {}

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

    # --- tools ---
    def load_tools(self, name: str, *, use_draft: bool = True) -> List[Tool]:
        if use_draft:
            tools = self._get_persisted_tools(name)
            self._draft_tools[name] = [tool.copy() for tool in tools]
            return [tool.copy() for tool in tools]

        return [tool.copy() for tool in self.get_published_tools(name)]

    def save_tools(self, name: str, tools: Sequence[Tool | dict]) -> tuple[List[Tool], bool]:
        recipe = self._load_recipe_config(name)
        coerced = self._coerce_tools(tools)
        recipe = recipe.with_tools(coerced)
        normalized = self._save_recipe_config(name, recipe)
        autosorted = self._locator_autosort.pop(name, False)
        self._draft_tools[name] = [tool.copy() for tool in normalized.tools]
        self.db.mark_recipe_draft_updated(name)
        return [tool.copy() for tool in normalized.tools], autosorted

    # --- multi-view steps ---
    def load_steps(self, name: str) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        return [step.copy() for step in recipe.steps]

    def save_steps(
        self,
        name: str,
        steps: Sequence[RecipeStep | dict],
        *,
        aggregation: str | None = None,
    ) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        recipe.steps = self._coerce_steps(steps)
        if aggregation is not None:
            recipe.aggregation_mode = self._normalize_aggregation(aggregation)
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return [step.copy() for step in normalized.steps]

    def add_step(
        self, name: str, step: RecipeStep | dict, *, index: Optional[int] = None
    ) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        steps = [entry.copy() for entry in recipe.steps]
        new_step = step.copy() if isinstance(step, RecipeStep) else RecipeStep.from_dict(step)
        if index is None or index >= len(steps):
            steps.append(new_step)
        else:
            insert_at = max(0, int(index))
            steps.insert(insert_at, new_step)
        recipe.steps = steps
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return [entry.copy() for entry in normalized.steps]

    def update_step(self, name: str, step_id: str, step: RecipeStep | dict) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        steps = [entry.copy() for entry in recipe.steps]
        target_index = next((idx for idx, entry in enumerate(steps) if entry.step_id == step_id), -1)
        if target_index < 0:
            raise ValueError(f"Step '{step_id}' not found")
        updated = step.copy() if isinstance(step, RecipeStep) else RecipeStep.from_dict(step)
        updated.step_id = step_id
        steps[target_index] = updated
        recipe.steps = steps
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return [entry.copy() for entry in normalized.steps]

    def remove_step(self, name: str, step_id: str) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        filtered = [step.copy() for step in recipe.steps if step.step_id != step_id]
        if len(filtered) == len(recipe.steps):
            raise ValueError(f"Step '{step_id}' not found")
        if not filtered:
            raise ValueError("Recipe must contain at least one step")
        recipe.steps = filtered
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return [entry.copy() for entry in normalized.steps]

    def reorder_steps(self, name: str, order: Sequence[str]) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        mapping = {step.step_id: step.copy() for step in recipe.steps}
        if set(order) != set(mapping.keys()):
            raise ValueError("order must reference each existing step exactly once")
        reordered = [mapping[step_id].copy() for step_id in order]
        recipe.steps = reordered
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return [entry.copy() for entry in normalized.steps]

    def set_step_tools(
        self, name: str, step_id: str, tools: Sequence[Tool | dict]
    ) -> List[RecipeStep]:
        recipe = self._load_recipe_config(name)
        coerced_tools = self._coerce_tools(tools)
        updated_steps: List[RecipeStep] = []
        replaced = False
        for step in recipe.steps:
            if step.step_id == step_id:
                updated_steps.append(
                    RecipeStep(
                        step_id=step.step_id,
                        name=step.name,
                        golden_path=step.golden_path,
                        tools=[tool.copy() for tool in coerced_tools],
                        camera_profile=step.camera_profile.copy()
                        if step.camera_profile
                        else None,
                        settle_ms=step.settle_ms,
                        weight=step.weight,
                    )
                )
                replaced = True
            else:
                updated_steps.append(step.copy())
        if not replaced:
            raise ValueError(f"Step '{step_id}' not found")
        recipe.steps = updated_steps
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return [entry.copy() for entry in normalized.steps]

    def set_aggregation_mode(self, name: str, mode: str) -> str:
        recipe = self._load_recipe_config(name)
        recipe.aggregation_mode = self._normalize_aggregation(mode)
        normalized = self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return normalized.aggregation_mode

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

    def _coerce_steps(self, steps: Sequence[RecipeStep | dict]) -> List[RecipeStep]:
        coerced: List[RecipeStep] = []
        for step in steps:
            if isinstance(step, RecipeStep):
                coerced.append(step.copy())
            elif isinstance(step, dict):
                coerced.append(RecipeStep.from_dict(step))
            else:
                raise TypeError(f"Unsupported step entry: {type(step)!r}")
        if not coerced:
            raise ValueError("Recipe must contain at least one step")
        return coerced

    @staticmethod
    def _normalize_aggregation(mode: str | None) -> str:
        if not isinstance(mode, str):
            return "AND"
        normalized = mode.strip().upper()
        if normalized not in {"AND", "OR", "WEIGHTED"}:
            return "AND"
        return normalized

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

# app/services/recipe_service.py
from pathlib import Path
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.schema import (
    DEFAULT_VIEW_ID,
    RecipeAggregation,
    RecipeData,
    RecipeV2,
    RecipeView,
    Tool,
)
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
        recipe = RecipeV2(
            pose_enabled=True,
            regions=[],
            tools=[],
            views=[RecipeView(id=DEFAULT_VIEW_ID, name="Default View", golden_path="golden.png")],
            aggregation=RecipeAggregation(),
        )
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

    # --- views ---
    def get_views(self, name: str) -> List[RecipeView]:
        recipe = self._load_recipe_config(name)
        return [self._copy_view(view) for view in recipe.views]

    def add_view(
        self,
        name: str,
        view: RecipeView | Dict[str, Any],
        *,
        duplicate_from: Optional[str] = None,
    ) -> Tuple[List[RecipeView], List[Tool]]:
        recipe = self._load_recipe_config(name)
        primary_view = recipe.primary_view_id
        new_view = self._coerce_view(view)
        recipe.views.append(new_view)

        source_view_id = (duplicate_from or primary_view).strip() or primary_view
        valid_ids = {entry.id for entry in recipe.views}
        if source_view_id not in valid_ids:
            source_view_id = primary_view

        clones: List[Tool] = []
        for tool in recipe.tools:
            current_view_id = (tool.view_id or primary_view).strip() or primary_view
            if current_view_id == source_view_id:
                clone = tool.copy()
                clone.view_id = new_view.id
                clones.append(clone)
        if clones:
            recipe.tools.extend(clones)

        normalized = self._save_recipe_config(name, recipe)
        self._draft_tools[name] = [tool.copy() for tool in normalized.tools]
        self.db.mark_recipe_draft_updated(name)
        return (
            [self._copy_view(view) for view in normalized.views],
            [tool.copy() for tool in normalized.tools],
        )

    def set_view_golden_path(
        self, name: str, view_id: str, golden_path: str
    ) -> List[RecipeView]:
        recipe = self._load_recipe_config(name)
        normalized_path = str(golden_path or "").strip()
        updated = False
        for view in recipe.views:
            if view.id == view_id:
                view.golden_path = normalized_path
                updated = True
                break
        if not updated:
            raise KeyError(f"Unknown view: {view_id}")

        normalized = self._save_recipe_config(name, recipe)
        self._draft_tools[name] = [tool.copy() for tool in normalized.tools]
        self.db.mark_recipe_draft_updated(name)
        return [self._copy_view(view) for view in normalized.views]

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
        working_copy = recipe.copy()
        normalized_tools, autosorted = self._normalize_tools(working_copy)

        publish_copy = RecipeV2(
            pose_enabled=recipe.pose_enabled,
            regions=[dict(region) for region in recipe.regions],
            tools=[tool.copy() for tool in normalized_tools],
            on_locator_failure=recipe.on_locator_failure,
            export_artifacts=recipe.export_artifacts,
            views=[self._copy_view(view) for view in recipe.views],
            aggregation=RecipeAggregation(
                mode=recipe.aggregation.mode,
                weights=dict(recipe.aggregation.weights),
            ),
        )

        self._save_published_recipe_config(name, publish_copy)
        self.db.mark_recipe_published(name)
        self._draft_tools[name] = [tool.copy() for tool in publish_copy.tools]
        return [tool.copy() for tool in publish_copy.tools], autosorted

    def get_published_tools(self, name: str) -> List[Tool]:
        recipe = self._load_published_recipe_config(name)
        return [tool.copy() for tool in self._sort_tools(recipe.tools, recipe.views)]

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

    def _coerce_view(self, view: RecipeView | Dict[str, Any]) -> RecipeView:
        if isinstance(view, RecipeView):
            return self._copy_view(view)
        if isinstance(view, dict):
            return RecipeView(**dict(view))
        raise TypeError(f"Unsupported view entry: {type(view)!r}")

    def _copy_view(self, view: RecipeView) -> RecipeView:
        return RecipeView(
            id=view.id,
            name=view.name,
            golden_path=view.golden_path,
            camera_profile=view.camera_profile,
            settle_ms=view.settle_ms,
        )

    def _get_persisted_tools(self, name: str) -> List[Tool]:
        recipe = self._load_recipe_config(name)
        return [tool.copy() for tool in self._sort_tools(recipe.tools, recipe.views)]

    def _sort_tools(
        self,
        tools: Iterable[Tool],
        views: Sequence[RecipeView] | None = None,
    ) -> List[Tool]:
        view_order = {view.id: idx for idx, view in enumerate(views or [])}
        default_view_id = (views[0].id if views else DEFAULT_VIEW_ID)
        default_rank = len(view_order)

        def sort_key(tool: Tool) -> tuple[int, int, str]:
            vid = tool.view_id or default_view_id
            return (view_order.get(vid, default_rank), int(tool.order), str(tool.name))

        return [tool.copy() for tool in sorted(tools, key=sort_key)]

    def _normalize_tools(self, recipe: RecipeV2) -> tuple[List[Tool], bool]:
        sorted_tools = self._sort_tools(recipe.tools, recipe.views)
        view_order = {view.id: idx for idx, view in enumerate(recipe.views)}
        default_view_id = recipe.primary_view_id
        grouped: Dict[str, List[Tool]] = {}
        for tool in sorted_tools:
            vid = tool.view_id or default_view_id
            grouped.setdefault(vid, []).append(tool)

        enforced: List[Tool] = []
        for vid in sorted(grouped.keys(), key=lambda key: view_order.get(key, len(view_order))):
            view_tools = grouped[vid]
            for tool in view_tools:
                if not tool.view_id:
                    tool.view_id = vid
            locators = [tool for tool in view_tools if tool.type.startswith("locator.")]
            analyzers = [tool for tool in view_tools if not tool.type.startswith("locator.")]
            enforced.extend(locators + analyzers)

        autosorted = enforced != sorted_tools
        normalized = [tool.with_order(idx) for idx, tool in enumerate(enforced)]
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
        normalized_tools, autosorted = self._normalize_tools(recipe_copy)
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

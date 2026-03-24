# app/services/recipe_service.py
from pathlib import Path
import json
from typing import Iterable, List, Sequence, Optional

from app.models.schema import (
    RecipeData,
    RecipeV2,
    Tool,
    RecipeView,
    ViewCameraProfile,
)
from app.services.db_service import DbService
from app.services.tool_service import ToolService, DEFAULT_THRESHOLDS
from app.services.storage_service import load_recipe_config, save_recipe_config

_UNSET = object()

class RecipeService:
    def __init__(self, base_dir="/data", db: DbService | None = None):
        self.base = Path(base_dir)
        self.db = db or DbService(self.base / "HDF_Vision.db")
        self.tool = ToolService(base_dir=base_dir)
        self._draft_tools: dict[str, dict[str, List[Tool]]] = {}
        self._locator_autosort: dict[tuple[str, str], bool] = {}

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

    def get_logging_enabled(self, name: str) -> bool:
        recipe = self._load_recipe_config(name)
        return bool(getattr(recipe, "logging_enabled", True))

    def set_logging_enabled(self, name: str, enabled: bool) -> bool:
        recipe = self._load_recipe_config(name)
        recipe.logging_enabled = bool(enabled)
        self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return bool(recipe.logging_enabled)

    # --- views ---
    def list_views(self, name: str) -> List[RecipeView]:
        recipe = self._load_recipe_config(name)
        return [view.copy() for view in recipe.views]

    def get_view(self, name: str, view_id: str | None = None) -> RecipeView:
        recipe = self._load_recipe_config(name)
        return recipe.get_view(view_id)

    @staticmethod
    def _normalize_trigger_mode(trigger_mode: str | None) -> str:
        mode = str(trigger_mode or "timed").strip().lower()
        if mode not in {"timed", "external", "manual"}:
            mode = "timed"
        return mode

    @classmethod
    def _normalize_trigger_interval(
        cls,
        trigger_mode: str | None,
        trigger_interval_ms: int | None,
    ) -> int | None:
        if cls._normalize_trigger_mode(trigger_mode) != "timed":
            return None
        return int(trigger_interval_ms) if trigger_interval_ms is not None else None

    @staticmethod
    def _normalize_trigger_gap(trigger_gap_ms: float | int | None) -> float | None:
        if trigger_gap_ms is None:
            return None
        value = float(trigger_gap_ms)
        return value if value > 0 else None

    def add_view(
        self,
        name: str,
        *,
        source_view_id: str | None = None,
        view_id: str | None = None,
        view_name: str | None = None,
        frame_source_view_id: str | None = None,
        camera_profile: ViewCameraProfile | dict | str | None = None,
        settle_ms: int | None = None,
        trigger_mode: str | None = None,
        external_trigger_mode: str | None | object = _UNSET,
        external_request_input: int | None | object = _UNSET,
        trigger_interval_ms: int | None = None,
        trigger_gap_ms: float | int | None = None,
        image_rotation: int | None = None,
        branch_enabled: bool | None = None,
        branch_targets: dict[str, str] | None = None,
        branch_default_view_id: str | None = None,
    ) -> RecipeView:
        import shutil

        recipe = self._load_recipe_config(name)
        existing_ids = {view.id for view in recipe.views}
        if view_id:
            candidate_id = str(view_id).strip()
            if not candidate_id:
                raise ValueError("View ID must be a non-empty string")
            if candidate_id in existing_ids:
                raise ValueError(f"View ID '{candidate_id}' already exists")
            new_id = candidate_id
        else:
            new_index = 1
            new_id = ""
            while not new_id:
                candidate = f"view_{new_index}"
                if candidate not in existing_ids:
                    new_id = candidate
                else:
                    new_index += 1

        existing_names = {view.name for view in recipe.views}
        if view_name:
            new_name = str(view_name).strip() or f"View {len(existing_names) + 1}"
        else:
            new_index_for_name = 1
            new_name = f"View {new_index_for_name}"
            while new_name in existing_names:
                new_index_for_name += 1
                new_name = f"View {new_index_for_name}"

        source_tools: List[Tool] = []
        source_camera: ViewCameraProfile | dict | str | None = None
        source_settle: Optional[int] = None
        source_trigger_mode: str | None = None
        source_external_trigger_mode: str | None = None
        source_external_request_input: int | None = None
        source_trigger_interval: Optional[int] = None
        source_trigger_gap: float | None = None
        source_golden: Optional[str] = None
        source_frame_source: Optional[str] = None
        source_image_rotation: int = 0
        source_branch_enabled: Optional[bool] = None
        source_branch_targets: Optional[dict[str, str]] = None
        source_branch_default: Optional[str] = None
        if source_view_id:
            try:
                source_view = recipe.get_view(source_view_id)
                profile = source_view.camera_profile
                if isinstance(profile, ViewCameraProfile):
                    source_camera = profile.copy()
                elif isinstance(profile, dict):
                    source_camera = dict(profile)
                else:
                    source_camera = profile
                source_settle = source_view.settle_ms
                source_trigger_mode = source_view.trigger_mode
                source_external_trigger_mode = getattr(
                    source_view, "external_trigger_mode", None
                )
                source_external_request_input = getattr(
                    source_view, "external_request_input", None
                )
                source_trigger_interval = source_view.trigger_interval_ms
                source_trigger_gap = getattr(source_view, "trigger_gap_ms", None)
                source_golden = source_view.golden_path
                source_image_rotation = int(getattr(source_view, "image_rotation", 0) or 0)
                source_frame_source = source_view.frame_source_view_id
                source_branch_enabled = getattr(source_view, "branch_enabled", None)
                source_branch_targets = getattr(source_view, "branch_targets", None)
                source_branch_default = getattr(
                    source_view, "branch_default_view_id", None
                )
                draft_tools = self._ensure_draft_tools(name, source_view_id, recipe)
                source_tools = [tool.copy() for tool in draft_tools]
            except Exception:
                source_tools = []

        golden_filename = f"golden_{new_id}.png"
        target_camera = camera_profile if camera_profile is not None else source_camera
        if isinstance(target_camera, ViewCameraProfile):
            camera_payload: ViewCameraProfile | dict | str | None = target_camera.copy()
        elif isinstance(target_camera, dict):
            camera_payload = dict(target_camera)
        else:
            camera_payload = target_camera

        target_settle = settle_ms if settle_ms is not None else source_settle
        target_trigger_mode = self._normalize_trigger_mode(trigger_mode or source_trigger_mode)
        target_external_trigger_mode = (
            external_trigger_mode
            if external_trigger_mode is not _UNSET
            else source_external_trigger_mode
        )
        target_external_request_input = (
            external_request_input
            if external_request_input is not _UNSET
            else source_external_request_input
        )
        target_trigger_interval = self._normalize_trigger_interval(
            target_trigger_mode,
            trigger_interval_ms if trigger_interval_ms is not None else source_trigger_interval,
        )
        target_trigger_gap = self._normalize_trigger_gap(
            trigger_gap_ms if trigger_gap_ms is not None else source_trigger_gap,
        )

        target_frame_source = frame_source_view_id
        if target_frame_source is None:
            target_frame_source = source_frame_source

        target_image_rotation = image_rotation if image_rotation is not None else source_image_rotation

        target_branch_enabled = (
            bool(branch_enabled)
            if branch_enabled is not None
            else bool(source_branch_enabled)
        )
        target_branch_targets = (
            dict(branch_targets)
            if branch_targets is not None
            else dict(source_branch_targets or {})
        )
        target_branch_default = (
            branch_default_view_id
            if branch_default_view_id is not None
            else source_branch_default
        )

        new_view = RecipeView(
            id=new_id,
            name=new_name,
            golden_path=golden_filename,
            frame_source_view_id=target_frame_source,
            camera_profile=camera_payload,
            settle_ms=target_settle,
            trigger_mode=target_trigger_mode,
            external_trigger_mode=target_external_trigger_mode,
            external_request_input=target_external_request_input,
            trigger_interval_ms=target_trigger_interval,
            trigger_gap_ms=target_trigger_gap,
            image_rotation=int(target_image_rotation or 0),
            tools=[],
            branch_enabled=target_branch_enabled,
            branch_targets=target_branch_targets,
            branch_default_view_id=target_branch_default,
        )
        if source_tools:
            new_view.set_tools(source_tools)

        recipe.views.append(new_view)
        normalized = self._save_recipe_config(name, recipe)
        normalized_view = normalized.get_view(new_id)

        # copy golden file if available
        if source_golden:
            src_path = Path(self.base) / "recipes" / name / source_golden
            dst_path = Path(self.base) / "recipes" / name / golden_filename
            if src_path.exists() and not dst_path.exists():
                try:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                except Exception:
                    pass

        drafts = self._draft_tools.setdefault(name, {})
        drafts[new_id] = [tool.copy() for tool in normalized_view.tools]
        self.db.mark_recipe_draft_updated(name)
        return normalized_view.copy()

    def update_view(
        self,
        name: str,
        view_id: str,
        *,
        view_name: str,
        frame_source_view_id: str | None,
        camera_profile: ViewCameraProfile | dict | str | None,
        settle_ms: int | None,
        trigger_mode: str,
        external_trigger_mode: str | None = None,
        external_request_input: int | None = None,
        trigger_interval_ms: int | None,
        trigger_gap_ms: float | int | None = None,
        image_rotation: int = 0,
        branch_enabled: bool = False,
        branch_targets: dict[str, str] | None = None,
        branch_default_view_id: str | None = None,
    ) -> RecipeView:
        recipe = self._load_recipe_config(name)
        view = recipe.get_view(view_id)

        normalized_name = str(view_name or "").strip()
        if not normalized_name:
            raise ValueError("View name must not be empty")

        if isinstance(camera_profile, ViewCameraProfile):
            camera_payload: ViewCameraProfile | dict | str | None = camera_profile.copy()
        elif isinstance(camera_profile, dict):
            camera_payload = dict(camera_profile)
        else:
            camera_payload = camera_profile

        normalized_mode = self._normalize_trigger_mode(trigger_mode)
        normalized_interval = self._normalize_trigger_interval(
            normalized_mode,
            trigger_interval_ms,
        )
        normalized_trigger_gap = self._normalize_trigger_gap(trigger_gap_ms)

        updated_view = RecipeView(
            id=view.id,
            name=normalized_name,
            golden_path=view.golden_path,
            frame_source_view_id=frame_source_view_id,
            camera_profile=camera_payload,
            settle_ms=settle_ms,
            trigger_mode=normalized_mode,
            external_trigger_mode=external_trigger_mode,
            external_request_input=external_request_input,
            trigger_interval_ms=normalized_interval,
            trigger_gap_ms=normalized_trigger_gap,
            image_rotation=int(image_rotation or 0),
            tools=[tool.copy() for tool in view.tools],
            branch_enabled=branch_enabled,
            branch_targets=dict(branch_targets or {}),
            branch_default_view_id=branch_default_view_id,
        )

        replaced: list[RecipeView] = []
        for existing in recipe.views:
            if existing.id == view.id:
                replaced.append(updated_view)
            else:
                replaced.append(existing.copy())
        recipe.views = replaced
        normalized = self._save_recipe_config(name, recipe)
        updated = normalized.get_view(view.id)
        drafts = self._draft_tools.setdefault(name, {})
        drafts.setdefault(view.id, [tool.copy() for tool in updated.tools])
        self.db.mark_recipe_draft_updated(name)
        return updated.copy()

    def remove_view(self, name: str, view_id: str) -> List[RecipeView]:
        recipe = self._load_recipe_config(name)
        if len(recipe.views) <= 1:
            raise ValueError("Recipe must contain at least one view")

        remaining: List[RecipeView] = []
        removed = False
        for view in recipe.views:
            if view.id == view_id:
                removed = True
                continue
            remaining.append(view)

        if not removed:
            raise KeyError(f"View '{view_id}' not found")

        recipe.views = [view.copy() for view in remaining]
        normalized = self._save_recipe_config(name, recipe)
        drafts = self._draft_tools.get(name)
        if drafts and view_id in drafts:
            drafts.pop(view_id, None)
        self._locator_autosort = {
            key: value
            for key, value in self._locator_autosort.items()
            if key[0] != name or key[1] != view_id
        }
        self.db.mark_recipe_draft_updated(name)
        return [view.copy() for view in normalized.views]

    # --- tools ---
    def load_tools(
        self, name: str, *, use_draft: bool = True, view_id: str | None = None
    ) -> List[Tool]:
        recipe = self._load_recipe_config(name)
        view = recipe.get_view(view_id)
        if use_draft:
            draft = self._ensure_draft_tools(name, view.id, recipe)
            return [tool.copy() for tool in draft]

        return [tool.copy() for tool in self.get_published_tools(name, view.id)]

    def save_tools(
        self,
        name: str,
        tools: Sequence[Tool | dict],
        *,
        view_id: str | None = None,
    ) -> tuple[List[Tool], bool]:
        recipe = self._load_recipe_config(name)
        view = recipe.get_view(view_id)
        coerced = self._coerce_tools(tools)
        view.set_tools(coerced)
        normalized = self._save_recipe_config(name, recipe)
        normalized_view = normalized.get_view(view.id)
        autosorted = self._locator_autosort.pop((name, view.id), False)
        self._draft_tools.setdefault(name, {})[view.id] = [
            tool.copy() for tool in normalized_view.tools
        ]
        self.db.mark_recipe_draft_updated(name)
        return [tool.copy() for tool in normalized_view.tools], autosorted

    def publish_recipe(
        self, name: str, *, view_id: str | None = None
    ) -> tuple[List[Tool], bool]:
        recipe = self._load_recipe_config(name)
        publish_copy = recipe.copy()
        autosorted_any = False
        for view in publish_copy.views:
            normalized_tools, autosorted = self._normalize_tools(view.tools)
            view.set_tools(normalized_tools)
            self._locator_autosort[(name, view.id)] = autosorted
            autosorted_any = autosorted_any or autosorted
        publish_copy._sync_tools_from_views()
        self._save_published_recipe_config(name, publish_copy)
        self.db.mark_recipe_published(name)
        self._draft_tools[name] = {
            view.id: [tool.copy() for tool in view.tools]
            for view in publish_copy.views
        }
        target_view = publish_copy.get_view(view_id)
        return [tool.copy() for tool in target_view.tools], autosorted_any

    def get_published_tools(self, name: str, view_id: str | None = None) -> List[Tool]:
        recipe = self._load_published_recipe_config(name)
        view = recipe.get_view(view_id)
        return [tool.copy() for tool in self._sort_tools(view.tools)]

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
    def get_draft_tools(self, name: str, view_id: str | None = None) -> List[Tool]:
        draft = self._ensure_draft_tools(name, view_id)
        return [tool.copy() for tool in draft]

    def add_tool(
        self, recipe_id: str, tool: Tool | dict, *, view_id: str | None = None
    ) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id, view_id)
        draft.append(Tool.from_dict(tool))
        self._normalize_draft_orders(draft, self._resolve_view_id(recipe_id, view_id))
        return [tool.copy() for tool in draft]

    def remove_tool(
        self, recipe_id: str, tool_index: int, *, view_id: str | None = None
    ) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id, view_id)
        if 0 <= tool_index < len(draft):
            del draft[tool_index]
            self._normalize_draft_orders(
                draft, self._resolve_view_id(recipe_id, view_id)
            )
        return [tool.copy() for tool in draft]

    def update_tool(
        self,
        recipe_id: str,
        tool_index: int,
        tool: Tool | dict,
        *,
        view_id: str | None = None,
    ) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id, view_id)
        if tool_index < 0 or tool_index >= len(draft):
            raise IndexError("tool_index out of range")
        draft[tool_index] = Tool.from_dict(tool)
        self._normalize_draft_orders(draft, self._resolve_view_id(recipe_id, view_id))
        return [entry.copy() for entry in draft]

    def reorder_tools(
        self,
        recipe_id: str,
        new_order: Sequence[int],
        *,
        view_id: str | None = None,
    ) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id, view_id)
        if len(new_order) != len(draft):
            raise ValueError("new_order must match number of tools")
        if sorted(new_order) != list(range(len(draft))):
            raise ValueError("new_order must be a permutation of current indices")
        reordered = [draft[idx] for idx in new_order]
        draft[:] = reordered
        self._normalize_draft_orders(draft, self._resolve_view_id(recipe_id, view_id))
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

    def _get_persisted_tools(
        self, name: str, view_id: str | None, recipe: RecipeV2 | None = None
    ) -> List[Tool]:
        recipe_obj = recipe or self._load_recipe_config(name)
        view = recipe_obj.get_view(view_id)
        return [tool.copy() for tool in self._sort_tools(view.tools)]

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

    def _ensure_draft_tools(
        self,
        name: str,
        view_id: str | None = None,
        recipe: RecipeV2 | None = None,
    ) -> List[Tool]:
        recipe_views = self._draft_tools.setdefault(name, {})
        resolved_view_id = self._resolve_view_id(name, view_id, recipe)
        draft = recipe_views.get(resolved_view_id)
        if draft is None:
            persisted = self._get_persisted_tools(name, resolved_view_id, recipe)
            draft = [tool.copy() for tool in persisted]
            recipe_views[resolved_view_id] = draft
        return draft

    def _normalize_draft_orders(self, draft: List[Tool], view_id: str) -> None:
        for idx, tool in enumerate(draft):
            tool.order = idx
            tool.view_id = view_id

    def _resolve_view_id(
        self,
        name: str,
        view_id: str | None,
        recipe: RecipeV2 | None = None,
    ) -> str:
        recipe_obj = recipe or self._load_recipe_config(name)
        return recipe_obj.get_view(view_id).id

    def _load_recipe_config(self, name: str) -> RecipeV2:
        recipe = load_recipe_config(name, base_dir=self.base)
        # ensure persisted if legacy fallback created
        self._ensure_recipe_file(name, recipe)
        return recipe

    def _save_recipe_config(self, name: str, recipe: RecipeV2) -> RecipeV2:
        recipe_copy = recipe.copy()
        for view in recipe_copy.views:
            normalized_tools, autosorted = self._normalize_tools(view.tools)
            view.set_tools(normalized_tools)
            self._locator_autosort[(name, view.id)] = autosorted
        recipe_copy._sync_tools_from_views()
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

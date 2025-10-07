# app/services/recipe_service.py
from pathlib import Path
import json
import imageio.v3 as iio
import numpy as np

from app.models.regions import Region
from app.models.schema import RecipeDefinition, ToolNode
from app.services.db_service import DbService
from app.services.tool_service import ToolService, DEFAULT_THRESHOLDS
from app.services.storage_service import save_tool_mask, delete_tool_mask

class RecipeService:
    def __init__(self, base_dir="/data", db: DbService | None = None):
        self.base = Path(base_dir)
        self.db = db or DbService()
        self.tool = ToolService(base_dir=base_dir)
        self.tool.pose_enabled = True

    def _recipe_dir(self, name: str) -> Path:
        return self.base / "recipes" / name

    def _recipe_json(self, name: str) -> Path:
        return self._recipe_dir(name) / "regions.json"

    def _load_definition(self, name: str) -> RecipeDefinition:
        path = self._recipe_json(name)
        if not path.exists():
            return RecipeDefinition()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RecipeDefinition.from_dict(data)

    def _persist(self, name: str, recipe: RecipeDefinition):
        recipe_dir = self._recipe_dir(name)
        recipe_dir.mkdir(parents=True, exist_ok=True)
        with open(self._recipe_json(name), "w", encoding="utf-8") as f:
            json.dump(recipe.to_dict(), f, ensure_ascii=False, indent=2)
        if getattr(self.tool, "recipe", None) == name:
            self.tool.recipe_def = recipe
            self.tool.pose_enabled = recipe.pose_enabled
            self.tool.regions = [r.to_dict() for r in recipe.regions]
            self.tool.tools = recipe.tools

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

    def save_recipe_data(
        self,
        name: str,
        pose_enabled: bool,
        regions: list[dict | Region],
        tools: list[dict | ToolNode] | None = None,
    ):
        recipe_dir = self._recipe_dir(name)
        recipe_dir.mkdir(parents=True, exist_ok=True)
        regions_list = [r if isinstance(r, Region) else Region.from_dict(r) for r in regions]
        tool_nodes: list[ToolNode] = []
        for t in tools or []:
            if isinstance(t, ToolNode):
                tool_nodes.append(t)
            else:
                tool_nodes.append(ToolNode.from_dict(t))
        recipe = RecipeDefinition(pose_enabled=pose_enabled, regions=regions_list, tools=tool_nodes)
        self._persist(name, recipe)

    def list_tools(self, name: str) -> list[ToolNode]:
        recipe = self._load_definition(name)
        return list(recipe.tools)

    def add_tool(self, name: str, tool: ToolNode) -> ToolNode:
        recipe = self._load_definition(name)
        if tool.ignore_mask is not None:
            tool.ignore_mask_path = save_tool_mask(tool.ignore_mask, name, tool.id)
        recipe.tools.append(tool)
        self._persist(name, recipe)
        return tool

    def update_tool(self, name: str, tool_id: str, **changes) -> ToolNode:
        recipe = self._load_definition(name)
        for idx, node in enumerate(recipe.tools):
            if node.id != tool_id:
                continue
            for key, value in changes.items():
                if key == "ignore_mask":
                    if value is None:
                        if node.ignore_mask_path:
                            delete_tool_mask(name, node.id)
                        node.ignore_mask = None
                        node.ignore_mask_path = None
                    else:
                        node.ignore_mask_path = save_tool_mask(value, name, node.id)
                        node.ignore_mask = value
                elif hasattr(node, key):
                    setattr(node, key, value)
            recipe.tools[idx] = node
            self._persist(name, recipe)
            return node
        raise KeyError(f"Tool {tool_id} neexistuje v recepte {name}.")

    def remove_tool(self, name: str, tool_id: str):
        recipe = self._load_definition(name)
        kept: list[ToolNode] = []
        for node in recipe.tools:
            if node.id == tool_id:
                if node.ignore_mask_path:
                    delete_tool_mask(name, node.id)
                continue
            kept.append(node)
        recipe.tools = kept
        self._persist(name, recipe)

    def reorder_tools(self, name: str, order: list[str]):
        recipe = self._load_definition(name)
        mapping = {node.id: node for node in recipe.tools}
        new_list: list[ToolNode] = []
        for tid in order:
            node = mapping.pop(tid, None)
            if node is not None:
                new_list.append(node)
        new_list.extend(mapping.values())
        recipe.tools = new_list
        self._persist(name, recipe)

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

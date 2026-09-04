"""Long-lived, read-only-from-the-UI recipe change audit."""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

from app.services.db_service import DbService


ACTIONS = frozenset({"CREATE", "UPDATE", "DELETE", "RENAME", "REPLACE", "PUBLISH"})


class RecipeAuditService:
    """Central API for writing and querying recipe audit events."""

    def __init__(self, db: DbService):
        self.db = db

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def log_change(
        self,
        recipe_name: str,
        *,
        action: str,
        entity_type: str,
        recipe_id: int | None = None,
        view_id: str | None = None,
        view_name: str | None = None,
        entity_id: str | None = None,
        entity_name: str | None = None,
        field_name: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
        source: str | None = None,
        details: Any = None,
        ts_ms: int | None = None,
        force: bool = False,
    ) -> int | None:
        action = action.upper()
        entity_type = entity_type.upper()
        if action not in ACTIONS:
            raise ValueError(f"Unsupported audit action: {action}")
        if not force and action == "UPDATE" and old_value == new_value:
            return None
        cur = self.db.conn().execute(
            """INSERT INTO recipe_change_log (
                 ts_ms, recipe_id, recipe_name, view_id, view_name, action,
                 entity_type, entity_id, entity_name, field_name,
                 old_value_json, new_value_json, source, details_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(ts_ms if ts_ms is not None else time.time() * 1000),
                recipe_id if recipe_id is not None else self.db.recipe_id(recipe_name),
                recipe_name, view_id, view_name, action, entity_type,
                entity_id, entity_name, field_name,
                self._json(old_value), self._json(new_value), source,
                self._json(details) if details is not None else None,
            ),
        )
        self.db.conn().commit()
        return int(cur.lastrowid)

    def list_changes(
        self,
        *,
        recipe_name: str | None = None,
        view_id: str | None = None,
        view_name: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        for column, value in (("recipe_name", recipe_name), ("view_id", view_id),
                              ("view_name", view_name), ("entity_type", entity_type)):
            if value:
                where.append(f"{column} = ? COLLATE NOCASE")
                params.append(value)
        if search and search.strip():
            columns = ("recipe_name", "view_name", "action", "entity_type", "entity_name",
                       "field_name", "old_value_json", "new_value_json", "source")
            where.append("(" + " OR ".join(f"COALESCE({c}, '') LIKE ? COLLATE NOCASE" for c in columns) + ")")
            params.extend([f"%{search.strip()}%"] * len(columns))
        sql = "SELECT * FROM recipe_change_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts_ms ASC, id ASC"
        cur = self.db.conn().execute(sql, params)
        names = [item[0] for item in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def distinct_recipe_names(self) -> list[str]:
        rows = self.db.conn().execute(
            "SELECT DISTINCT recipe_name FROM recipe_change_log ORDER BY recipe_name COLLATE NOCASE"
        )
        return [row[0] for row in rows]

    def distinct_views(self, recipe_name: str | None = None) -> list[tuple[str | None, str]]:
        sql = "SELECT DISTINCT view_id, view_name FROM recipe_change_log WHERE view_name IS NOT NULL"
        params: Iterable[Any] = ()
        if recipe_name:
            sql += " AND recipe_name=? COLLATE NOCASE"
            params = (recipe_name,)
        sql += " ORDER BY view_name COLLATE NOCASE"
        return [(row[0], row[1]) for row in self.db.conn().execute(sql, params)]


def flatten_changes(old: Any, new: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    """Return leaf-level differences, while keeping list geometry as one value."""
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        result: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.extend(flatten_changes(old.get(key), new.get(key), path))
        return result
    return [(prefix, old, new)]


def audit_recipe_diff(
    audit: RecipeAuditService,
    recipe_name: str,
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    source: str,
) -> None:
    """Record a semantic field-level diff after recipe.json was saved."""
    old_views = {str(v.get("id")): v for v in old.get("views", []) if isinstance(v, dict)}
    new_views = {str(v.get("id")): v for v in new.get("views", []) if isinstance(v, dict)}
    for view_id in sorted(old_views.keys() - new_views.keys()):
        view = old_views[view_id]
        audit.log_change(recipe_name, action="DELETE", entity_type="VIEW", view_id=view_id,
                         view_name=view.get("name"), entity_id=view_id, entity_name=view.get("name"),
                         old_value=view, new_value=None, source=source, force=True)
    for view_id in sorted(new_views.keys() - old_views.keys()):
        view = new_views[view_id]
        audit.log_change(recipe_name, action="CREATE", entity_type="VIEW", view_id=view_id,
                         view_name=view.get("name"), entity_id=view_id, entity_name=view.get("name"),
                         old_value=None, new_value=view, source=source, force=True)
    for view_id in sorted(old_views.keys() & new_views.keys()):
        before, after = old_views[view_id], new_views[view_id]
        view_name = str(after.get("name") or before.get("name") or view_id)
        old_tools = before.get("tools", [])
        new_tools = after.get("tools", [])
        before_base = {k: v for k, v in before.items() if k != "tools"}
        after_base = {k: v for k, v in after.items() if k != "tools"}
        for field, old_value, new_value in flatten_changes(before_base, after_base):
            entity_type = _entity_for_field(field)
            action = "RENAME" if field == "name" else "UPDATE"
            audit.log_change(recipe_name, action=action, entity_type=entity_type,
                             view_id=view_id, view_name=view_name, entity_id=view_id,
                             entity_name=view_name, field_name=field, old_value=old_value,
                             new_value=new_value, source=source)
        _audit_tools(audit, recipe_name, view_id, view_name, old_tools, new_tools, source)
    for field, old_value, new_value in flatten_changes(
        {k: v for k, v in old.items() if k not in {"views", "tools"}},
        {k: v for k, v in new.items() if k not in {"views", "tools"}},
    ):
        entity = ("ROI" if field == "regions" or field.startswith("regions.") else
                  "VALIDATION" if "validation" in field else
                  "LOCATOR" if "locator" in field else "RECIPE")
        audit.log_change(recipe_name, action="UPDATE", entity_type=entity, field_name=field,
                         old_value=old_value, new_value=new_value, source=source)


def _entity_for_field(field: str) -> str:
    if field.startswith("camera_profile") or field in {"image_rotation"}:
        return "CAMERA"
    if any(token in field for token in ("trigger", "external_", "flash_")):
        return "TRIGGER"
    if field.startswith("branch_") or field == "frame_source_view_id":
        return "VIEW"
    if "validation" in field:
        return "VALIDATION"
    return "VIEW"


def _audit_tools(audit: RecipeAuditService, recipe: str, view_id: str, view_name: str,
                 old_tools: list[Any], new_tools: list[Any], source: str) -> None:
    def keyed(items: list[Any]) -> dict[str, dict[str, Any]]:
        result = {}
        for index, item in enumerate(items):
            if isinstance(item, dict):
                key = str(item.get("id") or item.get("name") or index)
                result[key] = item
        return result
    before, after = keyed(old_tools), keyed(new_tools)
    for key in sorted(before.keys() - after.keys()):
        item = before[key]
        audit.log_change(recipe, action="DELETE", entity_type="TOOL", view_id=view_id,
                         view_name=view_name, entity_id=key, entity_name=item.get("name"),
                         old_value=item, new_value=None, source=source, force=True)
    for key in sorted(after.keys() - before.keys()):
        item = after[key]
        audit.log_change(recipe, action="CREATE", entity_type="TOOL", view_id=view_id,
                         view_name=view_name, entity_id=key, entity_name=item.get("name"),
                         old_value=None, new_value=item, source=source, force=True)
    for key in sorted(before.keys() & after.keys()):
        item = after[key]
        for field, old_value, new_value in flatten_changes(before[key], item):
            audit.log_change(recipe, action="RENAME" if field == "name" else "UPDATE",
                             entity_type="TOOL", view_id=view_id, view_name=view_name,
                             entity_id=key, entity_name=item.get("name"), field_name=field,
                             old_value=old_value, new_value=new_value, source=source)

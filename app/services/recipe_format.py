"""Recipe file I/O and validation against the installed tool registry."""
from __future__ import annotations

import json
from pathlib import Path
from app.models.recipe_contract import RecipeFormatError
from app.models.recipe_document import validate_recipe_document as _validate_document


def validate_recipe_document(data: object) -> dict:
    from app.services.tool_registry import ToolRegistry
    return _validate_document(data, supported_tool_types=ToolRegistry.list_tool_types())


def read_recipe_document(path: str | Path) -> dict:
    try:
        with Path(path).open(encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError) as exc:
        raise RecipeFormatError(f"Recept sa nedá načítať ({Path(path).name}): {exc}") from exc
    return validate_recipe_document(data)

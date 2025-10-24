"""Shared helpers for multi-view UI components."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.models.schema import RecipeView


def view_uses_global_golden(view: Optional[RecipeView]) -> bool:
    """Return ``True`` if the view should fall back to the legacy golden frame."""

    if view is None:
        return True

    golden_path = str(getattr(view, "golden_path", "") or "").strip()
    if not golden_path:
        return True

    return Path(golden_path).name == "golden.png"

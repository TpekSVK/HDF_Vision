"""Utilities for generating stable tool identifiers."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from app.models.schema import Tool


def compute_tool_identity(
    tool: Tool,
    *,
    fallback_index: Optional[int] = None,
    used_ids: Optional[Set[str]] = None,
) -> Tuple[str, str, int]:
    """Return a stable identifier, display label and order for ``tool``.

    ``fallback_index`` is used when ``tool.order`` cannot be converted to an
    integer. ``used_ids`` can be provided to guarantee uniqueness within the
    current context when multiple tools would otherwise share the same
    identifier. The returned tuple is ``(identifier, label, order)``.
    """

    try:
        order_value = int(getattr(tool, "order", fallback_index if fallback_index is not None else 0))
    except Exception:
        order_value = int(fallback_index or 0)

    base_label = str((getattr(tool, "name", "") or "").strip())
    if not base_label:
        base_label = str((getattr(tool, "type", "") or "").strip())
    if not base_label:
        base_label = f"tool_{order_value if order_value is not None else fallback_index or 0}"

    candidate = f"{order_value}:{base_label}"
    seen: Set[str]
    if used_ids is None:
        seen = set()
    else:
        seen = used_ids

    unique_id = candidate
    suffix = 2
    while unique_id in seen:
        unique_id = f"{candidate}#{suffix}"
        suffix += 1

    if used_ids is not None:
        used_ids.add(unique_id)

    return unique_id, base_label, order_value


__all__ = ["compute_tool_identity"]

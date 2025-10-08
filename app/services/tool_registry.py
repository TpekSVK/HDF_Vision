from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, TYPE_CHECKING

from app.models.schema import (
    Tool,
    ToolDefinition,
    ToolMask,
    ToolParams,
    ToolRoi,
    ToolThresholds,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.services.tool_service import ITool

ToolFactory = Callable[[], "ITool"]


@dataclass(slots=True)
class _ToolEntry:
    definition: ToolDefinition
    factory: ToolFactory


class ToolRegistry:
    """Registry that keeps tool definitions and corresponding factories."""

    def __init__(self) -> None:
        self._entries: Dict[str, _ToolEntry] = {}

    def register(self, definition: ToolDefinition, factory: ToolFactory) -> None:
        key = definition.type
        self._entries[key] = _ToolEntry(definition.copy(), factory)

    def unregister(self, type_id: str) -> None:
        self._entries.pop(type_id, None)

    def get_definition(self, type_id: str) -> Optional[ToolDefinition]:
        entry = self._entries.get(type_id)
        if entry is None:
            return None
        return entry.definition.copy()

    def list_tool_types(self) -> Iterable[str]:
        return sorted(self._entries.keys())

    def get_schema(self, type_id: str) -> Dict[str, Dict[str, Any]]:
        definition = self.get_definition(type_id)
        if definition is None:
            raise KeyError(f"Tool type '{type_id}' is not registered")
        schema = definition.meta.schema
        return {
            "params": {k: dict(v) for k, v in schema.params.items()},
            "thresholds": {k: dict(v) for k, v in schema.thresholds.items()},
        }

    def create(self, type_id: str) -> "ITool":
        entry = self._entries.get(type_id)
        if entry is None:
            raise KeyError(f"Tool type '{type_id}' is not registered")
        return entry.factory()

    def make_default_tool(self, type_id: str, *, name: str | None = None) -> Tool:
        definition = self.get_definition(type_id)
        if definition is None:
            raise KeyError(f"Tool type '{type_id}' is not registered")

        params_defaults = self._extract_defaults(definition.meta.schema.params)
        thresholds_defaults = self._extract_defaults(definition.meta.schema.thresholds)

        ignore_mask_supported = definition.meta.supports_ignore_mask

        return Tool(
            type=type_id,
            name=name or definition.display_name,
            enabled=True,
            order=0,
            roi=ToolRoi(),
            ignore_mask=ToolMask(None) if not ignore_mask_supported else ToolMask(),
            params=ToolParams(params_defaults),
            thresholds=ToolThresholds(thresholds_defaults),
        )

    @staticmethod
    def _extract_defaults(definitions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {}
        for key, spec in (definitions or {}).items():
            value = spec.get("default") if isinstance(spec, dict) else None
            defaults[key] = deepcopy(value)
        return defaults


registry = ToolRegistry()

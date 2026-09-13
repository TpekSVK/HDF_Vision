"""Recipe-bound tool catalog and golden reference loading."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional
import imageio.v3 as iio
import numpy as np
from app.models.schema import Tool, ToolDefinition


class ToolService:
    def __init__(self, base_dir="/data"):
        self.base = Path(base_dir)
        self.recipe = "default"
        self.golden = None            # np.ndarray uint8
        self.regions = None           # list[dict]
        self.pose_enabled = True



    # ------------------------------------------------------------------
    # Tool registry API
    # ------------------------------------------------------------------
    def list_tool_types(self) -> List[str]:
        """Return registered tool types."""

        from app.services.tool_registry import ToolRegistry

        return ToolRegistry.list_tool_types()

    def get_tool_meta(self, tool_type: str) -> ToolDefinition:
        """Retrieve metadata for a given tool type."""

        from app.services.tool_registry import ToolRegistry

        definition = ToolRegistry.get_tool_definition(tool_type)
        if definition is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")
        return definition

    def get_tool_schema(self, tool_type: str) -> Dict[str, Dict[str, Any]]:
        """Expose registry schema information for UI consumption."""

        from app.services.tool_registry import ToolRegistry

        return ToolRegistry.get_tool_schema(tool_type)

    def make_default_tool(self, tool_type: str, name: Optional[str] = None) -> Tool:
        """Create a ``Tool`` instance with registry defaults."""

        from app.services.tool_registry import ToolRegistry

        return ToolRegistry.make_default_tool(tool_type, name=name)

    def load_recipe(self, name: str):
        from app.services.storage_service import load_recipe_config
        config = load_recipe_config(name, base_dir=self.base)
        gfp = self.base / "recipes" / name / "golden.png"
        g = None
        if gfp.exists():
            g = iio.imread(gfp)
            if g.ndim == 3:
                g = g[:, :, 0]
            if g.dtype != np.uint8:
                peak = float(g.max())
                g = (g.astype(np.float32) * (255.0 / peak)).astype(np.uint8) if peak > 0 else np.zeros_like(g, dtype=np.uint8)
        self.recipe = name
        self.golden = g
        self.regions = config.regions
        self.pose_enabled = config.pose_enabled

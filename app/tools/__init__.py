"""High-level tool helpers exposed for external integrations."""

from .light_presence import (
    LightPresenceCheckParams,
    LightPresenceCheckTool,
    ToolContext as LightPresenceContext,
    ToolResult as LightPresenceResult,
)
from .light_transmission import (
    LightTransmissionCheckParams,
    LightTransmissionCheckTool,
    ToolContext as LightTransmissionContext,
    ToolResult as LightTransmissionResult,
)
from .presence_absence import (
    PresenceAbsenceCheckParams,
    PresenceAbsenceCheckTool,
    ToolContext as PresenceAbsenceContext,
    ToolResult as PresenceAbsenceResult,
)
from .presence_absence_v2 import PresenceAbsenceV2Tool

ToolContext = LightPresenceContext
ToolResult = LightPresenceResult

__all__ = [
    "LightPresenceCheckTool",
    "LightPresenceCheckParams",
    "LightPresenceContext",
    "LightPresenceResult",
    "LightTransmissionCheckTool",
    "LightTransmissionCheckParams",
    "LightTransmissionContext",
    "LightTransmissionResult",
    "PresenceAbsenceCheckTool",
    "PresenceAbsenceCheckParams",
    "PresenceAbsenceContext",
    "PresenceAbsenceResult",
    "PresenceAbsenceV2Tool",
    "ToolContext",
    "ToolResult",
]

"""Helpers for merging camera profile overrides."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.models.schema import ViewCameraProfile


def _normalize_camera_profile(
    profile: ViewCameraProfile | Mapping[str, Any] | str | None,
) -> Optional[ViewCameraProfile]:
    if isinstance(profile, ViewCameraProfile):
        return profile
    if isinstance(profile, Mapping):
        resolved = ViewCameraProfile.from_obj(dict(profile))
    else:
        resolved = ViewCameraProfile.from_obj(profile)
    return resolved if isinstance(resolved, ViewCameraProfile) else None


def resolve_view_camera_state(
    base: Mapping[str, Any] | None,
    profile: ViewCameraProfile | Mapping[str, Any] | str | None,
) -> dict[str, Any]:
    """Return camera state merged with optional per-view overrides."""

    state: dict[str, Any] = {}

    if isinstance(base, Mapping):
        for key in ("width", "height", "fps"):
            value = base.get(key)
            if value is None:
                continue
            try:
                state[key] = int(value)
            except Exception:
                continue
        pixel_format = base.get("pixel_format") if "pixel_format" in base else None
        if pixel_format is not None:
            text = str(pixel_format).strip().upper()
            if text:
                state["pixel_format"] = text
        exposure = base.get("exposure_us") if "exposure_us" in base else None
        if exposure is not None:
            try:
                state["exposure_us"] = int(exposure)
            except Exception:
                pass
        gain = base.get("gain_db") if "gain_db" in base else None
        if gain is not None:
            try:
                state["gain_db"] = float(gain)
            except Exception:
                pass

    profile_obj = _normalize_camera_profile(profile)
    if isinstance(profile_obj, ViewCameraProfile):
        if profile_obj.width is not None:
            state["width"] = int(profile_obj.width)
        if profile_obj.height is not None:
            state["height"] = int(profile_obj.height)
        if profile_obj.fps is not None:
            state["fps"] = int(profile_obj.fps)
        if profile_obj.pixel_format:
            state["pixel_format"] = profile_obj.pixel_format.upper()
        if profile_obj.exposure_us is not None:
            state["exposure_us"] = int(profile_obj.exposure_us)
        if profile_obj.gain_db is not None:
            state["gain_db"] = float(profile_obj.gain_db)

    return state


__all__ = ["resolve_view_camera_state"]

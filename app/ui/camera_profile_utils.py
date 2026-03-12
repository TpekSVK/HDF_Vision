"""Helpers for camera profile normalization/resolution and application."""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional

from app.models.schema import ViewCameraProfile

_LOGGER = logging.getLogger(__name__)


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
    """Return resolved camera state from base + per-view profile."""

    state: dict[str, Any] = {}

    if isinstance(base, Mapping):
        for key in (
            "device_id",
            "width",
            "height",
            "fps",
            "pixel_format",
            "exposure_us",
            "gain_db",
            "gamma",
            "brightness",
            "sharpness",
            "stream_mode",
            "flash_mode",
        ):
            if key in base and base.get(key) is not None:
                state[key] = base.get(key)

    profile_obj = _normalize_camera_profile(profile)
    if isinstance(profile_obj, ViewCameraProfile):
        for key in (
            "device_id",
            "width",
            "height",
            "fps",
            "pixel_format",
            "exposure_us",
            "gain_db",
            "gamma",
            "brightness",
            "sharpness",
            "stream_mode",
            "flash_mode",
        ):
            value = getattr(profile_obj, key)
            if value is not None:
                state[key] = value

    if state.get("pixel_format") is not None:
        state["pixel_format"] = str(state["pixel_format"]).strip().upper()

    return state


def snapshot_camera_state(camera: Any) -> dict[str, Any]:
    """Capture camera runtime state supported by the service."""

    state: dict[str, Any] = {}
    for key in ("device", "width", "height", "fps", "pixel_format", "exposure_us", "gain_db"):
        value = getattr(camera, key, None)
        if value is not None:
            mapped_key = "device_id" if key == "device" else key
            state[mapped_key] = value

    if state.get("pixel_format") is not None:
        state["pixel_format"] = str(state["pixel_format"]).strip().upper()

    for key, getter in (("stream_mode", "get_stream_mode"), ("flash_mode", "get_flash_mode")):
        method = getattr(camera, getter, None)
        if callable(method):
            try:
                state[key] = int(method())
            except Exception:
                pass

    return state


def apply_camera_state(
    camera: Any,
    state: Mapping[str, Any] | None,
    *,
    warn: Callable[[str], None] | None = None,
) -> None:
    """Apply fully resolved camera state to ``camera`` consistently."""

    if not isinstance(state, Mapping) or not state:
        return

    def _handle_error(message: str, exc: Exception) -> None:
        text = f"{message}: {exc}"
        if warn is not None:
            warn(text)
        else:
            raise RuntimeError(text) from exc

    if all(k in state for k in ("width", "height", "fps")):
        try:
            camera.apply_resolution(
                width=int(state["width"]),
                height=int(state["height"]),
                fps=int(state["fps"]),
                pixel_format=str(state.get("pixel_format") or "Y8").upper(),
            )
            _LOGGER.debug("Applied camera resolution/profile: %s", {k: state.get(k) for k in ("width", "height", "fps", "pixel_format")})
        except Exception as exc:
            _handle_error("Zmena rozlíšenia pre view zlyhala", exc)

    if "exposure_us" in state and state.get("exposure_us") is not None:
        try:
            camera.set_manual_exposure_us(int(state["exposure_us"]))
        except Exception as exc:
            _handle_error("Nastavenie expozície zlyhalo", exc)

    if "gain_db" in state and state.get("gain_db") is not None:
        try:
            camera.set_gain_db(int(round(float(state["gain_db"]))))
        except Exception as exc:
            _handle_error("Nastavenie gainu zlyhalo", exc)

    for key, setter in (("gamma", "set_gamma"), ("brightness", "set_brightness"), ("sharpness", "set_sharpness")):
        if key in state and state.get(key) is not None:
            method = getattr(camera, setter, None)
            if callable(method):
                try:
                    method(float(state[key]))
                except Exception as exc:
                    _handle_error(f"Nastavenie {key} zlyhalo", exc)

    for key, setter in (("stream_mode", "set_stream_mode"), ("flash_mode", "set_flash_mode")):
        if key in state and state.get(key) is not None:
            method = getattr(camera, setter, None)
            if callable(method):
                try:
                    method(int(state[key]))
                    _LOGGER.debug("Applied %s=%s", key, state[key])
                except Exception as exc:
                    _handle_error(f"Nastavenie {key} zlyhalo", exc)


def apply_view_camera_profile(
    camera: Any,
    base_state: Mapping[str, Any] | None,
    profile: ViewCameraProfile | Mapping[str, Any] | str | None,
    *,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Resolve and apply per-view camera profile."""

    state = resolve_view_camera_state(base_state or {}, profile)
    apply_camera_state(camera, state, warn=warn)
    return state


__all__ = [
    "apply_camera_state",
    "apply_view_camera_profile",
    "resolve_view_camera_state",
    "snapshot_camera_state",
]

"""Helpers for camera profile normalization/resolution and application."""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional

from app.models.schema import ViewCameraProfile

_LOGGER = logging.getLogger(__name__)

_DEFAULT_V4L2_CONTROLS = {
    "brightness",
    "exposure_time_absolute",
    "exposure_absolute",
    "gain",
    "gamma",
    "sharpness",
}


def _supported_v4l2_controls(camera: Any) -> set[str]:
    getter = getattr(camera, "get_supported_v4l2_controls", None)
    if callable(getter):
        try:
            controls = {str(item).strip() for item in getter() if str(item).strip()}
            if controls:
                return controls
        except Exception:
            pass
    return set(_DEFAULT_V4L2_CONTROLS)


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

    for key, getter in (("flash_mode", "get_flash_mode"),):
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
    apply_flash_mode: bool = True,
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

    supported_controls = _supported_v4l2_controls(camera)
    _LOGGER.info("Available V4L2 controls: %s", sorted(supported_controls))
    applied_controls: list[str] = []
    skipped_controls: list[str] = []

    if "exposure_us" in state and state.get("exposure_us") is not None:
        if not ({"exposure_time_absolute", "exposure_absolute"} & supported_controls):
            skipped_controls.append("exposure_us")
            _LOGGER.debug("Skipped unsupported V4L2 control: exposure_us")
        else:
            try:
                camera.set_manual_exposure_us(int(state["exposure_us"]))
                applied_controls.append("exposure_us")
            except Exception as exc:
                _handle_error("Nastavenie expozície zlyhalo", exc)
    if "gain_db" in state and state.get("gain_db") is not None:
        if "gain" not in supported_controls:
            skipped_controls.append("gain")
            _LOGGER.debug("Skipped unsupported V4L2 control: gain")
        else:
            try:
                camera.set_gain_db(int(round(float(state["gain_db"]))))
                applied_controls.append("gain")
            except Exception as exc:
                _handle_error("Nastavenie gainu zlyhalo", exc)

    for key, setter, control_name in (
        ("gamma", "set_gamma", "gamma"),
        ("brightness", "set_brightness", "brightness"),
        ("sharpness", "set_sharpness", "sharpness"),
    ):
        if key in state and state.get(key) is not None:
            if control_name not in supported_controls:
                skipped_controls.append(control_name)
                _LOGGER.debug("Skipped unsupported V4L2 control: %s", control_name)
                continue
            method = getattr(camera, setter, None)
            if callable(method):
                try:
                    method(float(state[key]))
                    applied_controls.append(control_name)
                except Exception as exc:
                    _handle_error(f"Nastavenie {key} zlyhalo", exc)

    if applied_controls:
        _LOGGER.info("Applied camera controls: %s", applied_controls)
    if skipped_controls:
        _LOGGER.info("Skipped unsupported camera controls: %s", skipped_controls)

    mode_setters = []
    if apply_flash_mode:
        mode_setters.append(("flash_mode", "set_flash_mode"))

    for key, setter in mode_setters:
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
    apply_flash_mode: bool = True,
) -> dict[str, Any]:
    """Resolve and apply per-view camera profile."""

    state = resolve_view_camera_state(base_state or {}, profile)
    apply_camera_state(
        camera,
        state,
        warn=warn,
        apply_flash_mode=apply_flash_mode,
    )
    return state


__all__ = [
    "apply_camera_state",
    "apply_view_camera_profile",
    "resolve_view_camera_state",
    "snapshot_camera_state",
]

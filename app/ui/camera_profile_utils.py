"""Helpers for merging camera profile overrides and applying them."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

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


def snapshot_camera_state(camera: Any) -> dict[str, Any]:
    """Capture the current resolution/exposure/gain from ``camera``."""

    state: dict[str, Any] = {}

    for key in ("width", "height", "fps"):
        value = getattr(camera, key, None)
        if value is None:
            continue
        try:
            state[key] = int(value)
        except Exception:
            continue

    pixel_format = getattr(camera, "pixel_format", None)
    if pixel_format:
        state["pixel_format"] = str(pixel_format).strip().upper()

    exposure = getattr(camera, "exposure_us", None)
    if exposure is not None:
        try:
            state["exposure_us"] = int(exposure)
        except Exception:
            pass

    gain = getattr(camera, "gain_db", None)
    if gain is not None:
        try:
            state["gain_db"] = float(gain)
        except Exception:
            pass

    return state


def apply_camera_state(
    camera: Any,
    state: Mapping[str, Any] | None,
    *,
    warn: Callable[[str], None] | None = None,
) -> None:
    """Apply the provided camera ``state`` to ``camera``.

    When ``warn`` is provided, recoverable errors (resolution/exposure/gain)
    are reported via the callback instead of raising.
    """

    if not isinstance(state, Mapping) or not state:
        return

    current = snapshot_camera_state(camera)

    def _handle_error(message: str, exc: Exception) -> None:
        text = f"{message}: {exc}"
        if warn is not None:
            warn(text)
        else:
            raise RuntimeError(text) from exc

    have_resolution = all(key in state for key in ("width", "height", "fps"))
    if have_resolution:
        target_resolution = {
            "width": int(state["width"]),
            "height": int(state["height"]),
            "fps": int(state["fps"]),
            "pixel_format": str(
                state.get("pixel_format")
                or current.get("pixel_format")
                or "Y8"
            ).upper(),
        }
        current_resolution = {
            "width": int(current.get("width", 0) or 0),
            "height": int(current.get("height", 0) or 0),
            "fps": int(current.get("fps", 0) or 0),
            "pixel_format": str(current.get("pixel_format") or "Y8").upper(),
        }
        if target_resolution != current_resolution:
            try:
                camera.apply_resolution(**target_resolution)
            except Exception as exc:  # pragma: no cover - hardware dependent
                _handle_error("Zmena rozlíšenia pre view zlyhala", exc)
            else:
                current.update(target_resolution)

    if "exposure_us" in state:
        try:
            exposure_value = int(state["exposure_us"])
        except Exception:
            exposure_value = None
        if (
            exposure_value is not None
            and current.get("exposure_us") != exposure_value
        ):
            try:
                camera.set_manual_exposure_us(exposure_value)
            except Exception as exc:  # pragma: no cover - hardware dependent
                _handle_error("Nastavenie expozície zlyhalo", exc)
            else:
                current["exposure_us"] = exposure_value

    if "gain_db" in state:
        try:
            gain_value = int(round(float(state["gain_db"])))
        except Exception:
            gain_value = None
        if gain_value is not None and current.get("gain_db") != gain_value:
            try:
                camera.set_gain_db(gain_value)
            except Exception as exc:  # pragma: no cover - hardware dependent
                _handle_error("Nastavenie gainu zlyhalo", exc)
            else:
                current["gain_db"] = gain_value


def apply_view_camera_profile(
    camera: Any,
    base_state: Mapping[str, Any] | None,
    profile: ViewCameraProfile | Mapping[str, Any] | str | None,
    *,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Resolve and apply a per-view camera profile."""

    state = resolve_view_camera_state(base_state or {}, profile)
    apply_camera_state(camera, state, warn=warn)
    return state


__all__ = [
    "apply_camera_state",
    "apply_view_camera_profile",
    "resolve_view_camera_state",
    "snapshot_camera_state",
]

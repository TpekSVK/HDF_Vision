"""The shared SETUP/Golden/RUN capture path for one camera and its Pico."""
import logging
from typing import Any
from collections.abc import Mapping
from numbers import Integral, Real
from app.services.camera_profiles import apply_view_camera_profile, snapshot_camera_state
from app.services.view_images import apply_view_rotation, apply_view_image_transform
from app.utils.trigger_timing import get_default_trigger_gap_ms


class ViewCapture:
    def __init__(self, camera, pico, pico_config, mode, active_view_id=None):
        self.cam, self.pico, self.pico_config = camera, pico, pico_config
        self.mode, self._active_view_id = mode, active_view_id
        self._logger = logging.getLogger(__name__)

    def master_frame(
        self, *, view: Any | None, capture_request_source: str,
    ):
        source = str(capture_request_source or "manual").lower()
        if source in {"pico", "picosoftware"}:
            raise RuntimeError("Pico snímka nemá rezervovaný frame.")
        view_id = getattr(view, "id", None) or self._active_view_id or "view_1"
        explicit = (
            str(getattr(view, "external_trigger_mode", "")).lower() == "explicit"
            and str(getattr(view, "external_source", "")).lower() == "pico"
        )
        if explicit:
            index = getattr(view, "external_request_input", None)
            if isinstance(index, bool) or not isinstance(index, Integral) or not 1 <= index <= 8:
                raise RuntimeError("Pohľad nemá platný Pico vstup.")
            target = f"IN{index}"
            if not self.pico_config.is_input_enabled(int(index)):
                raise RuntimeError("Pico vstup je zakázaný.")
        else:
            target = str(getattr(view, "pico_profile", None) or "").upper()
            if target not in {"V1", "V2"}:
                target = self.pico._normalize_target(view_id)
            if target not in {"V1", "V2"}:
                raise RuntimeError("Pohľad nemá platný Pico profil V1/V2.")
        self.cam.prepare_master_capture()
        return self.pico.capture_master(target, self.cam)


    def capture(
        self,
        *,
        trigger_mode_label: str,
        master_caller: str,
        view: Any | None = None,
        view_id: str | None = None,
        base_camera_state: Mapping[str, Any] | None = None,
        settle_ms: int | None = None,
        transform_stage: str = "inspection",
        image_rotation_override: int | None = None,
        capture_request_source: str = "manual",
        frame_request=None,
    ):
        if self.mode not in {"master", "trigger"}:
            raise ValueError("Neznámy režim snímania.")
        if view is None:
            raise ValueError("Snímanie vyžaduje konkrétny pohľad.")
        active_view = view
        active_view_id = getattr(active_view, "id", None) if active_view is not None else (view_id or self._active_view_id)
        self._logger.info("[VIEW_CAPTURE] active_view=%s", active_view_id)

        profile = getattr(active_view, "camera_profile", None) if active_view is not None else None
        self._logger.info("[VIEW_CAPTURE] applying camera profile")
        resolved_state = apply_view_camera_profile(
            self.cam,
            dict(base_camera_state) if isinstance(base_camera_state, Mapping) else snapshot_camera_state(self.cam),
            profile,
        )
        self._logger.info(
            "[VIEW_CAPTURE] resolved state width=%s height=%s fps=%s pixel_format=%s exposure=%s",
            resolved_state.get("width"),
            resolved_state.get("height"),
            resolved_state.get("fps"),
            resolved_state.get("pixel_format"),
            resolved_state.get("exposure_us"),
        )

        width = resolved_state.get("width") or getattr(self.cam, "width", None)
        height = resolved_state.get("height") or getattr(self.cam, "height", None)
        fps = resolved_state.get("fps") or getattr(self.cam, "fps", None)
        trigger_gap_ms = getattr(active_view, "trigger_gap_ms", None) if active_view is not None else None
        if not isinstance(trigger_gap_ms, (Integral, Real)) or float(trigger_gap_ms) <= 0:
            trigger_gap_ms = get_default_trigger_gap_ms(width, height, fps)
        trigger_gap_ms = float(trigger_gap_ms)
        self._logger.info("[VIEW_CAPTURE] resolved trigger_gap_ms=%.2f", trigger_gap_ms)

        mode = self.mode
        if mode == "master":
            frame = self.cam.wait_master_frame(frame_request) if frame_request is not None else self.master_frame(
                view=active_view,
                capture_request_source=capture_request_source,
            )
        self._logger.info(
            "[VIEW_CAPTURE] frame_capture_start source=%s capture_mode=%s active_view_id=%s settle_ms=%s",
            capture_request_source,
            mode,
            active_view_id,
            settle_ms,
        )
        # Legacy per-view settle_ms is ignored; Pico owns capture timing.

        if mode == "trigger":
            self.pico.prepare_trigger(self.cam)
            frame = self.pico.capture_trigger(self.cam, timeout_s=1.0)

        if image_rotation_override is not None:
            frame = apply_view_rotation(
                frame,
                int(image_rotation_override),
                context=str(active_view_id or "n/a"),
            )
            if transform_stage:
                self._logger.info("[VIEW_ROTATION] applied before %s", transform_stage)
        else:
            frame = apply_view_image_transform(frame, active_view, stage=transform_stage)
        return frame



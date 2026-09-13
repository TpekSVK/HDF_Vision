# app/services/camera_service.py
import os
import threading
import time
import logging
import subprocess
import re

from app.services.camera_hid_cu55 import CU55HID, map_video_to_hidraw, MODE_TRIGGER

from app.services.camera_pio import PioCapture
from app.services.camera_stream import CameraStream


class CameraService:

    """
    Unified capture služba:
      - preferuje GStreamer (v4l2src -> GRAY8 -> appsink)
      - fallback na OpenCV V4L2
    Vždy vracia uint8 (GRAY8). Ak príde Y12/Y16 -> prevedie sa na 8-bit.
    """

    def __init__(self,
                 device=None,
                 width=1920,
                 height=1080,
                 fps=60,
                 device_resolver=None):
        # WorkstationWindow supplies an explicit USB serial resolver; standalone callers may supply a path.
        self._device_resolver = device_resolver
        self.device = device or os.getenv("CAM_DEV", "/dev/video0")
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.pixel_format = "Y8"
        self.exposure_us = 8000
        self.gain_db = 0

        # runtime

        # backend info

        # GStreamer objekty

        # zásobník kandidátov zariadení
        self.devices = [self.device]

        self._paused_external = False
        self._last_open_args = {"device": self.devices[0] if self.devices else "/dev/video0",
                                "width": 1920, "height": 1080, "fps": 60, "fourcc": "GREY",
                                "pixel_format": self.pixel_format}
        self._hid: CU55HID | None = None
        self._logger = logging.getLogger(__name__)
        self._supported_v4l2_controls: set[str] | None = None
        self._camera_model: str | None = None
        self._trigger_primed = False
        self._trigger_priming_in_progress = False
        self._trigger_session_active = False
        self._trigger_session_ready = False
        self._trigger_last_capture_status = "normal"
        self._trigger_capture_active = False
        self._trigger_capture_depth = 0
        self._trigger_state_lock = threading.Lock()
        self.stream = CameraStream(self)
        self.pio = PioCapture(self)


    def resolve_device(self):
        if self._device_resolver is None:
            return self.device
        resolved = self._device_resolver()
        if resolved != self.device:
            self.stream.stop(caller="usb_identity_changed")
            self.device = resolved
            self.devices = [resolved]
            self._supported_v4l2_controls = None
            self._camera_model = None
        return resolved

    def prepare_master_capture(self):
        """Wait for actual streaming readiness before requesting any light pulse."""
        if self._paused_external:
            self.resume_after_external()
        self.stream.start(caller="prepare_master_capture")
        with self.stream._frame_condition:
            if time.monotonic() - self.stream._latest_delivery < 2.0 / max(1, self.fps):
                return
        request = self.stream.arm_master_frame()
        self.stream.wait_master_frame(request)

    def _log_trigger_cycle_state(self, event: str, **fields: object) -> None:
        payload = {
            "event": event,
            "stream_mode": fields.get("stream_mode"),
            "pipeline_open": bool(fields.get("pipeline_open", self.stream.is_pipeline_open())),
            "trigger_mode": bool(fields.get("trigger_mode", self._is_trigger_mode_active())),
            "preview_paused": bool(fields.get("preview_paused", False)),
            "trigger_primed": bool(fields.get("trigger_primed", self._trigger_primed)),
            "frame_received": bool(fields.get("frame_received", False)),
            "camera_open": bool(fields.get("camera_open", self.stream._cap is not None or self.stream._pipeline is not None)),
            "paused_external": bool(fields.get("paused_external", self._paused_external)),
            "fallback": fields.get("fallback"),
            "note": fields.get("note"),
        }
        self._logger.debug(
            "trigger_cycle event=%(event)s stream_mode=%(stream_mode)s pipeline_open=%(pipeline_open)s "
            "trigger_mode=%(trigger_mode)s preview_paused=%(preview_paused)s trigger_primed=%(trigger_primed)s "
            "frame_received=%(frame_received)s camera_open=%(camera_open)s paused_external=%(paused_external)s "
            "fallback=%(fallback)s note=%(note)s",
            payload,
        )

    def _is_preview_path_active(self) -> bool:
        """
        Preview path je queue/appsink + ring buffer pre UI live náhľad.
        TODO: mixed queue + ring capture ponechať iba do migrácie trigger path na blocking read model.
        """
        return bool(self.stream._pipeline is not None or self.stream._cap is not None)

    def _is_trigger_path_active(self) -> bool:
        """Trigger path je cap.read() flow pre trigger mode capture."""
        return bool(self._is_trigger_mode_active() and self.stream.is_pipeline_open())

    def _set_trigger_capture_active(self, active: bool) -> None:
        with self._trigger_state_lock:
            self._trigger_capture_active = bool(active)
            self._trigger_capture_depth = 1 if self._trigger_capture_active else 0

    def _is_trigger_capture_active(self) -> bool:
        with self._trigger_state_lock:
            return bool(self._trigger_capture_active or self._trigger_capture_depth > 0)

    def begin_trigger_capture(self) -> None:
        with self._trigger_state_lock:
            self._trigger_capture_depth += 1
            self._trigger_capture_active = self._trigger_capture_depth > 0
            in_progress = self._trigger_capture_active
        self._logger.debug("trigger_capture_in_progress %s", in_progress)

    def end_trigger_capture(self) -> None:
        with self._trigger_state_lock:
            self._trigger_capture_depth = max(0, self._trigger_capture_depth - 1)
            self._trigger_capture_active = self._trigger_capture_depth > 0
            in_progress = self._trigger_capture_active
        self._logger.debug("trigger_capture_in_progress %s", in_progress)

    def is_trigger_capture_in_progress(self) -> bool:
        in_progress = self._is_trigger_capture_active()
        self._logger.debug("trigger_capture_in_progress %s", in_progress)
        return in_progress


    def get_hid_device(self) -> str | None:
        hid = self._hid
        if hid is not None:
            return hid.hidraw_path
        try:
            return map_video_to_hidraw(self.device)
        except Exception:
            return None

    def _init_hid(self):
        if self._hid is not None:
            return
        try:
            hid_path = map_video_to_hidraw(self.device)
            if not hid_path:
                self._logger.warning("No HID device mapped for %s", self.device)
                return
            self._hid = CU55HID(hid_path)
            self._hid.open()
        except Exception as exc:
            self._logger.exception("Failed to initialize HID control: %s", exc)
            self._hid = None

    # =========================
    # GStreamer časť (preferovaná)
    # =========================


    # =========================
    # V4L2 fallback cez OpenCV
    # =========================


    # =========================
    # Public API
    # =========================


    def pause_for_external(self):
        """Uvoľní zariadenie pre externý klient (Live vo WIZARDe)."""
        if self._paused_external:
            return
        if self._trigger_session_active:
            self._logger.info("external preview requested while trigger session active")
            self.exit_trigger_session(restore_master=False)
        # zapamätaj poslednú config
        self._last_open_args.update({
            "device": self.device,
            "width": int(self.width),
            "height": int(self.height),
            "fps": int(self.fps),
            "fourcc": "GREY",
            "pixel_format": self.pixel_format,
        })
        # zastav všetko (cap aj GStreamer pipeline)
        self.stream.stop(caller="pause_for_external")
        self._paused_external = True
        print("[CameraService] paused for external access")

    def resume_after_external(self):
        """Znovu otvorí kameru s poslednými parametrami a rozbehne capture."""
        if not self._paused_external:
            return
        args = self._last_open_args
        self.device = args.get("device", self.device)
        self.width  = int(args.get("width", self.width))
        self.height = int(args.get("height", self.height))
        self.fps    = int(args.get("fps", self.fps))
        self.pixel_format = args.get("pixel_format", self.pixel_format)
        self._hid = None
        self.stream.start(caller="resume_after_external")
        self._paused_external = False
        print("[CameraService] resumed after external access")

    def apply_resolution(self, *, width: int, height: int, fps: int, pixel_format: str | None = None):
        width = int(width)
        height = int(height)
        fps = int(fps)
        pix_fmt = (pixel_format or self.pixel_format or "Y8").upper()
        current = {
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "pixel_format": self.pixel_format,
        }
        was_running = any([self.stream._cap is not None, self.stream._pipeline is not None, self.stream._mode])
        unchanged = (
            int(self.width) == width
            and int(self.height) == height
            and int(self.fps) == fps
            and str(self.pixel_format or "Y8").upper() == pix_fmt
        )
        if was_running and unchanged:
            self.stream._log_reuse_existing_pipeline("apply_resolution")
            return
        if was_running:
            self.stream.stop(caller="apply_resolution")
        self.width = width
        self.height = height
        self.fps = fps
        self.pixel_format = pix_fmt
        self.stream._reset_buffers()
        self._last_open_args.update({
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "pixel_format": self.pixel_format,
        })
        if was_running:
            try:
                self.stream.start(caller="apply_resolution")
            except Exception as exc:
                self.width = current["width"]
                self.height = current["height"]
                self.fps = current["fps"]
                self.pixel_format = current["pixel_format"]
                self._last_open_args.update({
                    "width": self.width,
                    "height": self.height,
                    "fps": self.fps,
                    "pixel_format": self.pixel_format,
                })
                try:
                    self.stream.start(caller="apply_resolution:rollback")
                except Exception:
                    pass
                raise RuntimeError(f"Camera reopen failed: {exc}") from exc

    def _run_v4l2_ctl(self, arg: str) -> bool:
        self.resolve_device()
        cmd = ["v4l2-ctl", "-d", self.device, "-c", arg]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self._logger.debug("Applied V4L2 control on %s: %s", self.device, arg)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self._logger.debug("Failed V4L2 control on %s: %s (%s)", self.device, arg, exc)
            return False

    def _query_v4l2_controls(self) -> set[str]:
        self.resolve_device()
        cmd = ["v4l2-ctl", "-d", self.device, "--list-ctrls"]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return set()
        controls: set[str] = set()
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or ":" not in stripped:
                continue
            name = stripped.split(":", 1)[0].strip().split()[0]
            if re.match(r"^[a-z0-9_]+$", name):
                controls.add(name)
        return controls

    def _query_camera_model(self) -> str | None:
        self.resolve_device()
        cmd = ["v4l2-ctl", "-d", self.device, "--all"]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() == "card type":
                model = value.strip()
                return model or None
        return None

    def _is_cu55_model(self) -> bool:
        model = (self._camera_model or "").lower()
        return "see3cam" in model and "cu55" in model

    def get_supported_v4l2_controls(self, *, refresh: bool = False) -> set[str]:
        if self._supported_v4l2_controls is not None and not refresh:
            return set(self._supported_v4l2_controls)

        discovered = self._query_v4l2_controls()
        self._camera_model = self._query_camera_model()
        if self._is_cu55_model():
            supported = {"brightness", "exposure_time_absolute"}
            if "exposure_absolute" in discovered:
                supported.add("exposure_absolute")
        elif discovered:
            supported = discovered
        else:
            supported = {
                "brightness",
                "exposure_time_absolute",
                "exposure_absolute",
                "gain",
                "gamma",
                "sharpness",
            }

        self._supported_v4l2_controls = set(supported)
        self._logger.info(
            "Detected V4L2 controls for %s (model=%s): %s",
            self.device,
            self._camera_model or "unknown",
            sorted(self._supported_v4l2_controls),
        )
        return set(self._supported_v4l2_controls)

    def get_camera_model(self) -> str | None:
        if self._camera_model is None:
            self.get_supported_v4l2_controls()
        return self._camera_model

    def _ensure_hid(self) -> CU55HID:
        self.resolve_device()
        if self._hid is None:
            self._init_hid()
        if self._hid is None:
            raise RuntimeError(f"HID control not available for {self.device}")
        return self._hid


    def _is_trigger_mode_active(self) -> bool:
        try:
            return int(self.get_stream_mode()) == int(MODE_TRIGGER)
        except Exception:
            return False

    def is_trigger_session_active(self) -> bool:
        return bool(self._trigger_session_active)


    def exit_trigger_session(self, *, restore_master: bool = False) -> None:
        self._logger.info("[TRIGGER_SESSION] exit")
        current_mode: int | None = None
        try:
            current_mode = int(self.get_stream_mode())
        except Exception:
            current_mode = None
        self._logger.info("current mode before exit=%s", current_mode)

        self._trigger_session_active = False
        self._trigger_session_ready = False
        self._trigger_primed = False
        self._trigger_priming_in_progress = False
        self.end_trigger_capture()
        if self.stream.is_pipeline_open():
            self.stream.stop(caller="exit_trigger_session")
        self.stream._clear_queue()

        if restore_master and self._is_cu55_model():
            self.set_stream_mode(0)
            self._logger.info("switched to master on exit")


    def get_last_trigger_capture_status(self) -> str:
        return str(getattr(self, "_trigger_last_capture_status", "normal") or "normal")


    def set_stream_mode(self, mode: int, *, stabilize_delay_s: float = 0.05):
        requested = int(mode)
        pipeline_open = self.stream.is_pipeline_open()
        hid_dev = self.get_hid_device()

        current: int | None = None
        try:
            current = int(self._ensure_hid().get_stream_mode())
        except Exception as exc:
            self._logger.debug("Get stream mode before set failed on %s (%s): %s", self.device, hid_dev, exc)
            if requested == 1:
                raise RuntimeError("TRIGGER zablokovaný: aktuálny režim kamery nie je známy.") from exc

        self._logger.debug(
            "stream mode request=%s current=%s pipeline_open=%s video_device=%s hid_device=%s",
            requested,
            current,
            pipeline_open,
            self.device,
            hid_dev,
        )

        if current is not None and current == requested:
            self._logger.debug("stream mode already set, skipping")
            return

        self._trigger_primed = False
        self._trigger_session_ready = False

        restarted = False
        if pipeline_open:
            self.stream.stop(caller="set_stream_mode")
            restarted = True

        try:
            self._ensure_hid().set_stream_mode(requested)
            self._logger.debug("stream mode HID set executed (mode=%s)", requested)
            if stabilize_delay_s > 0:
                time.sleep(float(stabilize_delay_s))
        except Exception:
            self._logger.exception("stream mode HID set failed (mode=%s)", requested)
            raise
        finally:
            if restarted:
                self.stream.start(caller="set_stream_mode")

    def get_stream_mode(self) -> int:
        try:
            return self._ensure_hid().get_stream_mode()
        except Exception as exc:
            self._logger.error("Get stream mode failed: %s", exc)
            return 0

    def set_flash_mode(self, mode: int):
        self._ensure_hid().set_flash_mode(mode)
        self._logger.debug("Set flash mode=%s on %s", int(mode), self.device)

    def get_flash_mode(self) -> int:
        try:
            return self._ensure_hid().get_flash_mode()
        except Exception as exc:
            self._logger.error("Get flash mode failed: %s", exc)
            return 0

    def read_firmware_version(self) -> tuple[int, int, int, int]:
        try:
            return self._ensure_hid().read_firmware_version()
        except Exception as exc:
            self._logger.error("Read firmware version failed: %s", exc)
            return (0, 0, 0, 0)

    def read_unique_id(self) -> str:
        try:
            return self._ensure_hid().read_unique_id()
        except Exception as exc:
            self._logger.error("Read unique ID failed: %s", exc)
            return ""

    def set_manual_exposure_us(self, exposure_us: int):
        val = int(exposure_us)
        if self._camera_model is None:
            self.get_supported_v4l2_controls()
        if val <= 0:
            raise RuntimeError("Set exposure failed: exposure must be positive")
        control_value = val // 100 if self._is_cu55_model() else val
        if self._is_cu55_model() and (val < 100 or val % 100):
            raise ValueError("CU55 expozícia musí byť násobkom 100 µs.")
        if self._is_cu55_model() and self.exposure_us == val and self._read_cu55_exposure() == control_value:
            return
        self.pio._invalidate_pio()
        if not self._is_cu55_model():
            self._run_v4l2_ctl("exposure_auto=1")
        if not self._run_v4l2_ctl(f"exposure_time_absolute={control_value}"):
            hundred_us = max(1, val // 100)
            if not self._run_v4l2_ctl(f"exposure_absolute={hundred_us}"):
                raise RuntimeError("Set exposure failed: v4l2-ctl command failed")
        if self._is_cu55_model():
            if self._read_cu55_exposure() != control_value:
                raise RuntimeError("CU55 exposure readback nesúhlasí.")
        if self.exposure_us != val:
            self.pio._invalidate_pio()
        self.exposure_us = val

    def _read_cu55_exposure(self):
        result = subprocess.run(["v4l2-ctl", "-d", self.device, "-C", "exposure_time_absolute"], capture_output=True, text=True, check=True)
        match = re.search(r"exposure_time_absolute\s*:\s*(\d+)", result.stdout)
        if not match:
            raise RuntimeError("CU55 exposure readback chýba.")
        return int(match[1])

    def set_gamma(self, value: float):
        val = int(round(float(value)))
        if not self._run_v4l2_ctl(f"gamma={val}"):
            raise RuntimeError("Set gamma failed: v4l2-ctl command failed")

    def set_brightness(self, value: float):
        val = int(round(float(value)))
        if getattr(self, "_brightness", None) == val:
            return
        self.pio._invalidate_pio()
        if not self._run_v4l2_ctl(f"brightness={val}"):
            raise RuntimeError("Set brightness failed: v4l2-ctl command failed")
        self._brightness = val

    def set_sharpness(self, value: float):
        val = int(round(float(value)))
        if not self._run_v4l2_ctl(f"sharpness={val}"):
            raise RuntimeError("Set sharpness failed: v4l2-ctl command failed")

    def set_gain_db(self, gain_db: int):
        val = int(gain_db)
        if val < 0:
            raise RuntimeError("Set gain failed: gain must be non-negative")
        if not self._run_v4l2_ctl(f"gain={val}"):
            raise RuntimeError("Set gain failed: v4l2-ctl command failed")
        self.gain_db = val

    def prepare_pio_trigger(self, pico):
        self.resolve_device()
        return self.pio.prepare_pio_trigger(pico)

    def prepare_pio_master(self, pico):
        self.resolve_device()
        return self.pio.prepare_pio_master(pico)

    def capture_pio_frame(self, pico, timeout_s=1.0):
        self.resolve_device()
        return self.pio.capture_pio_frame(pico, timeout_s=timeout_s)

    def arm_master_frame(self, *args, **kwargs):
        return self.stream.arm_master_frame(*args, **kwargs)

    def finish_master_frame(self, *args, **kwargs):
        return self.stream.finish_master_frame(*args, **kwargs)

    def wait_master_frame(self, *args, **kwargs):
        return self.stream.wait_master_frame(*args, **kwargs)

    def gst_start_count(self, *args, **kwargs):
        return self.stream.gst_start_count(*args, **kwargs)

    def is_pipeline_open(self, *args, **kwargs):
        return self.stream.is_pipeline_open(*args, **kwargs)

    def start(self, *args, **kwargs):
        return self.stream.start(*args, **kwargs)

    def one_shot(self, *args, **kwargs):
        return self.stream.one_shot(*args, **kwargs)

    def stop(self, *args, **kwargs):
        return self.stream.stop(*args, **kwargs)

    def start_continuous(self, *args, **kwargs):
        return self.stream.start_continuous(*args, **kwargs)

    def last_frame(self, *args, **kwargs):
        return self.stream.last_frame(*args, **kwargs)

    def discard_frames(self, *args, **kwargs):
        return self.stream.discard_frames(*args, **kwargs)

    def stop_continuous(self, *args, **kwargs):
        return self.stream.stop_continuous(*args, **kwargs)

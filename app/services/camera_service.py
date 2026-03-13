# app/services/camera_service.py
import os
import cv2
import numpy as np
import threading
import time
import queue
import logging
import subprocess
import re
from collections import deque
from collections.abc import Callable

from app.services.camera_hid_cu55 import CU55HID, map_video_to_hidraw, MODE_TRIGGER

# --- GStreamer (gst-python) je voliteľný, ale odporúčaný na Jetson-e
_GST_OK = False
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib
    Gst.init(None)
    _GST_OK = True
except Exception:
    _GST_OK = False


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
                 fps=60):
        # device môže prísť z env (run.sh nastavuje CAM_DEV=/dev/video0|1)
        self.device = device or os.getenv("CAM_DEV", "/dev/video0")
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.pixel_format = "Y8"
        self.exposure_us = 8000
        self.gain_db = 0

        # runtime
        self._q = queue.Queue(maxsize=5)
        self._stop = threading.Event()
        self._t = None

        # backend info
        self._mode = None  # "gst" alebo "v4l2"
        self._cap = None

        # GStreamer objekty
        self._pipeline = None
        self._loop = None

        # zásobník kandidátov zariadení
        self.devices = [self.device, "/dev/video0", "/dev/video1"]

        self._ring = deque(maxlen=5)
        self._t_ring = None
        self._stop_ring = threading.Event()
        self._paused_external = False
        self._last_open_args = {"device": self.devices[0] if self.devices else "/dev/video0",
                                "width": 1920, "height": 1080, "fps": 60, "fourcc": "GREY",
                                "pixel_format": self.pixel_format}
        self._hid: CU55HID | None = None
        self._logger = logging.getLogger(__name__)
        self._supported_v4l2_controls: set[str] | None = None
        self._camera_model: str | None = None
        self._active_pipeline_signature: dict[str, object] | None = None
        self._gst_start_count = 0
        self._trigger_primed = False
        self._trigger_priming_in_progress = False

    def _pipeline_signature(self, *, device: str | None = None) -> dict[str, object]:
        dev = device or self.device
        return {
            "device": str(dev or ""),
            "width": int(self.width),
            "height": int(self.height),
            "fps": int(self.fps),
            "pixel_format": str(self.pixel_format or "Y8").upper(),
        }

    def _pipeline_caps(self, signature: dict[str, object]) -> str:
        return (
            f"format={signature.get('pixel_format')},"
            f"{signature.get('width')}x{signature.get('height')}@{signature.get('fps')}"
        )

    def gst_start_count(self) -> int:
        return int(self._gst_start_count)

    def _log_start_pipeline(self, signature: dict[str, object], caller: str) -> None:
        self._logger.info(
            "start_pipeline(requested_device=%s, requested_caps=%s, caller=%s)",
            signature.get("device"),
            self._pipeline_caps(signature),
            caller,
        )

    def _log_reuse_existing_pipeline(self, caller: str) -> None:
        self._logger.info("reuse_existing_pipeline(caller=%s)", caller)

    def _log_stop_pipeline(self, caller: str) -> None:
        self._logger.info("stop_pipeline(caller=%s)", caller)

    def _log_latest_frame_used(self, caller: str) -> None:
        self._logger.info("latest_frame_used(caller=%s)", caller)

    def is_pipeline_open(self) -> bool:
        return bool(self._cap is not None or self._pipeline is not None or self._mode)

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
    def _gst_pipeline_str(self, dev, use_convert=False, with_fps=True):
        """
        GRAY8 caps podľa gst-device-monitor (u teba potvrdené).
        Skúšame viac permutácií (s/bez videoconvert, s/bez framerate caps).
        """
        caps = f"video/x-raw,format={self._gst_caps_format()},width={self.width},height={self.height}"
        if with_fps:
            caps += f",framerate={self.fps}/1"

        if use_convert:
            return (
                f"v4l2src device={dev} io-mode=2 ! "
                f"videoconvert ! "
                f"{caps} ! "
                f"appsink name=sink emit-signals=true sync=false drop=true max-buffers=2"
            )
        else:
            return (
                f"v4l2src device={dev} io-mode=2 ! "
                f"{caps} ! "
                f"appsink name=sink emit-signals=true sync=false drop=true max-buffers=2"
            )

    def _gst_caps_format(self) -> str:
        fmt = (self.pixel_format or "Y8").upper()
        if fmt in {"Y8", "GRAY8", "GREY"}:
            return "GRAY8"
        if fmt in {"Y12", "Y16", "GRAY16", "GRAY16_LE"}:
            return "GRAY16_LE"
        return "GRAY8"

    def _v4l2_fourcc(self) -> str:
        fmt = (self.pixel_format or "Y8").upper()
        if fmt in {"Y12", "Y16"}:
            return "Y12 "
        return "GREY"

    def _reset_buffers(self):
        self._q = queue.Queue(maxsize=5)
        self._ring = deque(maxlen=5)

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, map_info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = int(s.get_value("width"))
            h = int(s.get_value("height"))
            # GRAY8 by mal byť width*height bajtov
            arr = np.frombuffer(map_info.data, dtype=np.uint8)
            arr = arr.reshape((h, -1))[:, :w].copy()
            if self._q.full():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
            self._q.put_nowait(arr)
        finally:
            buf.unmap(map_info)
        return Gst.FlowReturn.OK

    def _gst_bus_cb(self, bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"[GST][ERROR] {err} debug:{dbg}")
        elif t == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            print(f"[GST][WARN] {err} debug:{dbg}")
        elif t == Gst.MessageType.EOS:
            print("[GST] EOS")
            self.stop()

    def _start_gst(self, dev):
        if not _GST_OK:
            return False

        # poradie variantov: najprv bez konverzie, potom s konverziou; s fps a bez fps
        variants = [
            self._gst_pipeline_str(dev, use_convert=False, with_fps=True),
            self._gst_pipeline_str(dev, use_convert=True,  with_fps=True),
            self._gst_pipeline_str(dev, use_convert=False, with_fps=False),
            self._gst_pipeline_str(dev, use_convert=True,  with_fps=False),
            # úplný fallback – bez caps (nech negociáciu spraví GSt, appsink dostane čo príde)
            f"v4l2src device={dev} io-mode=2 ! appsink name=sink emit-signals=true sync=false drop=true max-buffers=2",
        ]

        tried = []
        for pipe in variants:
            try:
                pipeline = Gst.parse_launch(pipe)
            except Exception as e:
                tried.append(("parse_fail", str(e), pipe))
                continue

            sink = pipeline.get_by_name("sink")
            if sink is None:
                tried.append(("no_sink", "", pipe))
                pipeline.set_state(Gst.State.NULL)
                continue
            sink.connect("new-sample", self._on_new_sample)

            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._gst_bus_cb)

            loop = GLib.MainLoop()

            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                tried.append(("PLAYING_fail", "", pipe))
                pipeline.set_state(Gst.State.NULL)
                continue

            # uložiť runtime objekty a spustiť loop v thread-e
            self._pipeline = pipeline
            self._loop = loop
            self._mode = "gst"

            def _loop_run():
                try:
                    loop.run()
                except Exception as e:
                    print("[GST] MainLoop exception:", e)

            self._t = threading.Thread(target=_loop_run, daemon=True)
            self._t.start()
            self._gst_start_count += 1
            print(f"[Camera] GST started: {pipe}")
            return True

        print("[GST] All variants failed:", tried)
        return False

    # =========================
    # V4L2 fallback cez OpenCV
    # =========================
    def _start_v4l2(self, dev):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            return False

        # zníž buffre, vypni RGB konverziu
        try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception: pass
        try: cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        except Exception: pass

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

        # preferuj GREY/Y800
        try:
            fourcc_primary = cv2.VideoWriter_fourcc(*self._v4l2_fourcc())
            if not cap.set(cv2.CAP_PROP_FOURCC, fourcc_primary):
                fourcc_y800 = cv2.VideoWriter_fourcc(*"Y800")
                cap.set(cv2.CAP_PROP_FOURCC, fourcc_y800)
        except Exception:
            pass

        if not cap.isOpened():
            cap.release()
            return False

        self._cap = cap
        self._mode = "v4l2"
        self._stop.clear()
        self._t = threading.Thread(target=self._grab_loop, daemon=True)
        self._t.start()
        print(f"[Camera] V4L2 started on {dev} {self.width}x{self.height}@{self.fps} {self.pixel_format}")
        return True

    def _grab_loop(self):
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.005)
                continue

            # ak príde BGR (niektoré buildy), preveď na greyscale
            if frame.ndim == 3 and frame.shape[2] == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # --- kľúčové: 16-bit -> 8-bit (Y12/Y16 normalizácia) ---
            if frame.dtype == np.uint16:
                maxv = int(frame.max())
                if maxv <= 0:
                    frame = np.zeros_like(frame, dtype=np.uint8)
                elif maxv <= 4095:          # typicky Y12
                    frame = (frame >> 4).astype(np.uint8)
                elif maxv <= 1023:          # ak by sa objavil 10-bit
                    frame = (frame >> 2).astype(np.uint8)
                else:                        # plný 16-bit
                    frame = cv2.convertScaleAbs(frame, alpha=255.0/65535.0)

            try:
                if self._q.full():
                    _ = self._q.get_nowait()
                self._q.put_nowait(frame)
            except queue.Full:
                pass

    # =========================
    # Public API
    # =========================
    def start(self, *, caller: str = "unspecified"):
        requested_signature = self._pipeline_signature()
        if self.is_pipeline_open():
            if self._active_pipeline_signature == requested_signature:
                self._log_reuse_existing_pipeline(caller)
                return False
            self.stop(caller=f"{caller}:restart")

        self._log_start_pipeline(requested_signature, caller)
        # poskladaj kandidátov tak, aby bol self.device prvý a bez duplicít
        seen = set()
        devs = []
        for d in [self.device] + list(self.devices):
            if d and d not in seen:
                devs.append(d); seen.add(d)

        # 1) GStreamer
        if _GST_OK:
            for dev in devs:
                if self._start_gst(dev):
                    self.device = dev
                    self._active_pipeline_signature = self._pipeline_signature(device=dev)
                    self._hid = None
                    self._init_hid()
                    self._last_open_args.update({
                        "device": dev,
                        "width": int(self.width),
                        "height": int(self.height),
                        "fps": int(self.fps),
                        "fourcc": "GREY",
                        "pixel_format": self.pixel_format,
                    })
                    self.get_supported_v4l2_controls(refresh=True)
                    return True

        # 2) Fallback: OpenCV V4L2
        for dev in devs:
            if self._start_v4l2(dev):
                self.device = dev
                self._active_pipeline_signature = self._pipeline_signature(device=dev)
                self._hid = None
                self._init_hid()
                self._last_open_args.update({
                    "device": dev,
                    "width": int(self.width),
                    "height": int(self.height),
                    "fps": int(self.fps),
                    "fourcc": "GREY",
                    "pixel_format": self.pixel_format,
                })
                self.get_supported_v4l2_controls(refresh=True)
                return True

        # nič sa neotvorilo
        raise RuntimeError("Camera open failed (V4L2 and GStreamer). Check /dev/video* and formats.")

    def one_shot(self):
        tries = 0
        last = None
        while tries < 3:
            try:
                last = self._q.get(timeout=0.5)
            except queue.Empty:
                tries += 1
                continue
            tries += 1
        if last is None:
            raise RuntimeError("No frame available for one-shot.")
        return last

    def stop(self, *, caller: str = "unspecified"):
        self._log_stop_pipeline(caller)
        self._stop.set()
        # V4L2
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        # GST
        if self._pipeline is not None:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None
        if self._loop is not None:
            try:
                self._loop.quit()
            except Exception:
                pass
            self._loop = None
        if self._t is not None and self._t.is_alive():
            self._t.join(timeout=1.0)
        self._t = None
        self._mode = None
        self._active_pipeline_signature = None
        self._trigger_primed = False
        self._trigger_priming_in_progress = False
        if self._hid is not None:
            try:
                self._hid.close()
            except Exception:
                pass
            self._hid = None
        self._reset_buffers()
        self.stop_continuous()

    def start_continuous(self):
        """Spustí ľahký kontinuálny zber do ring bufferu (bez GStreamer UI)."""
        if self._t_ring and self._t_ring.is_alive():
            return
        self._stop_ring.clear()
        self._t_ring = threading.Thread(target=self._loop_ring, daemon=True)
        self._t_ring.start()

    def _loop_ring(self):
        import time
        while not self._stop_ring.is_set():
            if self._cap is None:
                time.sleep(0.01)
                continue
            ok, frame = self._cap.read()
            if not ok or frame is None:
                time.sleep(0.002); continue
            if frame.ndim == 3 and frame.shape[2] == 1:
                frame = frame[:, :, 0]
            if frame.dtype != np.uint8:
                import cv2
                f = frame
                # normalizácia 16->8 (konzistentne)
                if f.dtype == np.uint16:
                    # Y12 -> 8-bit
                    f8 = cv2.convertScaleAbs(f, alpha=255.0/4095.0)
                else:
                    f8 = cv2.convertScaleAbs(f)
                frame = f8
            self._ring.append(frame)

    def last_frame(self, *, caller: str = "unspecified"):
        """Vráti posledný frame z kontinuálneho zberu, inak spraví rýchly oneshot ako fallback."""
        if self._ring:
            self._log_latest_frame_used(caller)
            return self._ring[-1]
        self._log_latest_frame_used(caller)
        return self.one_shot()

    def stop_continuous(self):
        try:
            self._stop_ring.set()
        except Exception:
            pass

    def pause_for_external(self):
        """Uvoľní zariadenie pre externý klient (Live vo WIZARDe)."""
        if self._paused_external:
            return
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
        self.stop(caller="pause_for_external")
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
        self.start(caller="resume_after_external")
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
        was_running = any([self._cap is not None, self._pipeline is not None, self._mode])
        unchanged = (
            int(self.width) == width
            and int(self.height) == height
            and int(self.fps) == fps
            and str(self.pixel_format or "Y8").upper() == pix_fmt
        )
        if was_running and unchanged:
            self._log_reuse_existing_pipeline("apply_resolution")
            return
        if was_running:
            self.stop(caller="apply_resolution")
        self.width = width
        self.height = height
        self.fps = fps
        self.pixel_format = pix_fmt
        self._reset_buffers()
        self._last_open_args.update({
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "pixel_format": self.pixel_format,
        })
        if was_running:
            try:
                self.start(caller="apply_resolution")
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
                    self.start(caller="apply_resolution:rollback")
                except Exception:
                    pass
                raise RuntimeError(f"Camera reopen failed: {exc}") from exc

    def _run_v4l2_ctl(self, arg: str) -> bool:
        cmd = ["v4l2-ctl", "-d", self.device, "-c", arg]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self._logger.debug("Applied V4L2 control on %s: %s", self.device, arg)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self._logger.debug("Failed V4L2 control on %s: %s (%s)", self.device, arg, exc)
            return False

    def _query_v4l2_controls(self) -> set[str]:
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
        if self._hid is None:
            self._init_hid()
        if self._hid is None:
            raise RuntimeError(f"HID control not available for {self.device}")
        return self._hid

    def _clear_queue(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _is_trigger_mode_active(self) -> bool:
        try:
            return int(self.get_stream_mode()) == int(MODE_TRIGGER)
        except Exception:
            return False

    def _send_software_trigger(self, *, note: str) -> None:
        hid = self._ensure_hid()
        hid.send_software_trigger()
        self._logger.info("%s", note)

    def _fire_trigger(self, *, note: str, trigger_fn: Callable[[], None] | None = None) -> None:
        if trigger_fn is not None:
            trigger_fn()
            self._logger.info("%s", note)
            return
        self._send_software_trigger(note=note)

    def ensure_trigger_pipeline_primed(
        self,
        *,
        settle_delay_s: float = 0.05,
        trigger_fn: Callable[[], None] | None = None,
    ) -> bool:
        if not self.is_pipeline_open():
            self.start(caller="trigger_pipeline_open")
            self._logger.info("trigger pipeline opened")
        if not self._is_trigger_mode_active():
            self._trigger_primed = False
            return False
        if self._trigger_primed:
            return True
        self._trigger_priming_in_progress = True
        try:
            self._logger.info("priming started")
            self._clear_queue()
            if settle_delay_s > 0:
                time.sleep(float(settle_delay_s))
            self._fire_trigger(note="priming dummy trigger sent", trigger_fn=trigger_fn)
            try:
                _ = self._q.get(timeout=0.5)
            except queue.Empty as exc:
                raise RuntimeError("Priming failed: no frame after dummy trigger") from exc
            self._logger.info("priming frame discarded")
            self._trigger_primed = True
            self._logger.info("camera primed")
            return True
        finally:
            self._trigger_priming_in_progress = False

    def capture_trigger_frame(self, *, timeout_s: float = 0.6, trigger_fn: Callable[[], None] | None = None):
        if not self._is_trigger_mode_active():
            return self.last_frame(caller="capture_trigger_frame:master")

        self.ensure_trigger_pipeline_primed(trigger_fn=trigger_fn)
        self._clear_queue()
        started = time.monotonic()
        self._fire_trigger(note="production trigger sent", trigger_fn=trigger_fn)
        try:
            frame = self._q.get(timeout=float(timeout_s))
        except queue.Empty as exc:
            raise RuntimeError("No frame received after production trigger") from exc
        latency_ms = (time.monotonic() - started) * 1000.0
        self._logger.info("frame received latency=%.2f ms", latency_ms)
        self._logger.info("frame reused for display+inspection")
        return frame

    def set_stream_mode(self, mode: int, *, stabilize_delay_s: float = 0.05):
        requested = int(mode)
        pipeline_open = self.is_pipeline_open()
        hid_dev = self.get_hid_device()

        current: int | None = None
        try:
            current = int(self._ensure_hid().get_stream_mode())
        except Exception as exc:
            self._logger.debug("Get stream mode before set failed on %s (%s): %s", self.device, hid_dev, exc)

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

        restarted = False
        if pipeline_open:
            self.stop(caller="set_stream_mode")
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
                self.start(caller="set_stream_mode")

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
        if val <= 0:
            raise RuntimeError("Set exposure failed: exposure must be positive")
        self._run_v4l2_ctl("exposure_auto=1")
        if not self._run_v4l2_ctl(f"exposure_time_absolute={val}"):
            hundred_us = max(1, val // 100)
            if not self._run_v4l2_ctl(f"exposure_absolute={hundred_us}"):
                raise RuntimeError("Set exposure failed: v4l2-ctl command failed")
        self.exposure_us = val

    def set_gamma(self, value: float):
        val = int(round(float(value)))
        if not self._run_v4l2_ctl(f"gamma={val}"):
            raise RuntimeError("Set gamma failed: v4l2-ctl command failed")

    def set_brightness(self, value: float):
        val = int(round(float(value)))
        if not self._run_v4l2_ctl(f"brightness={val}"):
            raise RuntimeError("Set brightness failed: v4l2-ctl command failed")

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

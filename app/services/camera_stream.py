"""One camera's stream, buffers and frame reservations. No mode policy."""
import cv2
import numpy as np
import threading
import time
import queue
from collections import deque
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


class CameraStream:
    def __init__(self, camera):
        self.camera = camera
        self._q = queue.Queue(maxsize=5)
        self._stop = threading.Event()
        self._t = None
        self._mode = None  # "gst" alebo "v4l2"
        self._cap = None
        self._pipeline = None
        self._loop = None
        self._sink = None
        self._bus = None
        self._delivery_lock = threading.RLock()
        self._ring = deque(maxlen=5)
        self._t_ring = None
        self._stop_ring = threading.Event()
        self._active_pipeline_signature: dict[str, object] | None = None
        self._gst_start_count = 0
        self._cap_read_lock = threading.Lock()
        self._frame_condition = threading.Condition()
        self._frame_requests = []
        self._latest_delivery = 0.0

    def arm_master_frame(self):
        """Reserve the first newly acquired frame, independently of preview reads."""
        request = {"after": time.monotonic(), "frame": None, "cancelled": False}
        with self._frame_condition:
            self._frame_requests.append(request)
        return request


    def finish_master_frame(self, request):
        with self._frame_condition:
            if request["frame"] is None:
                request["cancelled"] = True
            self._frame_condition.notify_all()


    def wait_master_frame(self, request, timeout_s=1.0):
        deadline = time.monotonic() + timeout_s
        with self._frame_condition:
            try:
                while request["frame"] is None and not request["cancelled"]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Čerstvá snímka po Pico CAPTURE nie je dostupná.")
                    self._frame_condition.wait(remaining)
                if request["cancelled"]:
                    raise RuntimeError("Capture skončil alebo sa kamera reštartovala bez čerstvej snímky.")
                self.camera._logger.info("[MASTER_CAPTURE] fresh frame ready elapsed_ms=%.1f",
                                  (time.monotonic() - request["after"]) * 1000.0)
                return request["frame"]
            finally:
                self._frame_requests = [item for item in self._frame_requests if item is not request]


    def _publish_master_frame(self, frame, acquired_at):
        with self._frame_condition:
            self._latest_delivery = time.monotonic()
            self._frame_requests = [request for request in self._frame_requests
                                    if self._latest_delivery - request["after"] < 2.0]
            for request in self._frame_requests:
                if request["frame"] is None and acquired_at >= request["after"]:
                    request["frame"] = frame
            self._frame_condition.notify_all()


    def _normalize_frame_u8(self, frame):
        """Zjednotená normalizácia frame do uint8 grayscale."""
        if frame is None:
            return None
        if frame.ndim == 3 and frame.shape[2] == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        elif frame.ndim == 3 and frame.shape[2] == 1:
            frame = frame[:, :, 0]

        if frame.dtype == np.uint16:
            maxv = int(frame.max())
            if maxv <= 0:
                return np.zeros_like(frame, dtype=np.uint8)
            if maxv <= 1023:
                return (frame >> 2).astype(np.uint8)
            if maxv <= 4095:
                return (frame >> 4).astype(np.uint8)
            return cv2.convertScaleAbs(frame, alpha=255.0 / 65535.0)

        if frame.dtype != np.uint8:
            return cv2.convertScaleAbs(frame)
        return frame


    def _pipeline_signature(self, *, device: str | None = None) -> dict[str, object]:
        dev = device or self.camera.device
        return {
            "device": str(dev or ""),
            "width": int(self.camera.width),
            "height": int(self.camera.height),
            "fps": int(self.camera.fps),
            "pixel_format": str(self.camera.pixel_format or "Y8").upper(),
        }


    def _pipeline_caps(self, signature: dict[str, object]) -> str:
        return (
            f"format={signature.get('pixel_format')},"
            f"{signature.get('width')}x{signature.get('height')}@{signature.get('fps')}"
        )


    def gst_start_count(self) -> int:
        return int(self._gst_start_count)


    def _log_start_pipeline(self, signature: dict[str, object], caller: str) -> None:
        self.camera._logger.info(
            "start_pipeline(requested_device=%s, requested_caps=%s, caller=%s)",
            signature.get("device"),
            self._pipeline_caps(signature),
            caller,
        )


    def _log_reuse_existing_pipeline(self, caller: str) -> None:
        self.camera._logger.info("reuse_existing_pipeline(caller=%s)", caller)


    def _log_stop_pipeline(self, caller: str) -> None:
        self.camera._logger.info("stop_pipeline(caller=%s)", caller)


    def _log_latest_frame_used(self, caller: str) -> None:
        self.camera._logger.info("latest_frame_used(caller=%s)", caller)


    def is_pipeline_open(self) -> bool:
        return bool(self._cap is not None or self._pipeline is not None or self._mode)


    def _gst_pipeline_str(self, dev, use_convert=False, with_fps=True):
        """
        GRAY8 caps podľa gst-device-monitor (u teba potvrdené).
        Skúšame viac permutácií (s/bez videoconvert, s/bez framerate caps).
        """
        caps = f"video/x-raw,format={self._gst_caps_format()},width={self.camera.width},height={self.camera.height}"
        if with_fps:
            caps += f",framerate={self.camera.fps}/1"

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
        fmt = (self.camera.pixel_format or "Y8").upper()
        if fmt in {"Y8", "GRAY8", "GREY"}:
            return "GRAY8"
        if fmt in {"Y12", "Y16", "GRAY16", "GRAY16_LE"}:
            return "GRAY16_LE"
        return "GRAY8"


    def _v4l2_fourcc(self) -> str:
        fmt = (self.camera.pixel_format or "Y8").upper()
        if fmt in {"Y12", "Y16"}:
            return "Y12 "
        return "GREY"


    def _reset_buffers(self):
        self.camera._logger.debug("reset buffers")
        with self._frame_condition:
            for request in self._frame_requests:
                request["cancelled"] = True
            self._latest_delivery = 0.0
            self._frame_condition.notify_all()
        self._clear_queue()
        self._clear_ring()
        self._q = queue.Queue(maxsize=5)
        self._ring = deque(maxlen=5)


    def _on_new_sample(self, sink):
        # Retired pipelines may still have a callback in flight after reconnect.
        with self._delivery_lock:
            if sink is not self._sink:
                return Gst.FlowReturn.OK
            return self._consume_gst_sample(sink)

    def _consume_gst_sample(self, sink):
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
            arr = self._normalize_frame_u8(arr)
            if arr is None:
                return Gst.FlowReturn.OK
            # Translate buffer running-time to the host monotonic clock. Reject
            # pre-event exposure/queued buffers instead of counting queue pops.
            acquired_at = time.monotonic()
            if buf.pts != Gst.CLOCK_TIME_NONE and self._pipeline is not None:
                clock = self._pipeline.get_clock()
                if clock is not None:
                    running = clock.get_time() - self._pipeline.get_base_time()
                    acquired_at -= max(0, running - buf.pts) / Gst.SECOND
            acquired_at -= max(1.0 / max(1, self.camera.fps), self.camera.exposure_us / 1_000_000.0)
            self.camera.pio._publish_pio_frame(arr, int(buf.offset))
            self._publish_master_frame(arr, acquired_at)
            # Preview-only path: queue/appsink slúži pre UI/live stream.
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
        with self._delivery_lock:
            if bus is not self._bus:
                return
            event = msg.type
            if event == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self.camera.pio._pio_pipeline_error = str(err)
                with self.camera.pio._pio_condition:
                    self.camera.pio._pio_condition.notify_all()
                self.camera._logger.error("GST %s: %s (%s)", self.camera.device, err, debug)
            elif event == Gst.MessageType.WARNING:
                err, debug = msg.parse_warning()
                self.camera._logger.warning("GST %s: %s (%s)", self.camera.device, err, debug)
        if event == Gst.MessageType.EOS:
            self.stop(caller="gst_eos", expected_bus=bus)

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

        if self.camera.pio._pio_configuring or self.camera.pio._pio_ready is not None:
            variants = variants[:1]
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
            context = GLib.MainContext.new()
            context.push_thread_default()
            try:
                bus.add_signal_watch()
                bus.connect("message", self._gst_bus_cb)
                loop = GLib.MainLoop.new(context, False)
            finally:
                context.pop_thread_default()
            self._sink, self._bus = sink, bus

            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                tried.append(("PLAYING_fail", "", pipe))
                self._sink, self._bus = None, None
                bus.remove_signal_watch()
                pipeline.set_state(Gst.State.NULL)
                continue

            # uložiť runtime objekty a spustiť loop v thread-e
            self._pipeline = pipeline
            self._loop = loop
            self._mode = "gst"
            self.camera._logger.debug("backend mode selected=%s device=%s", self._mode, dev)
            self.camera._logger.debug("pipeline open backend=%s device=%s", self._mode, dev)

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


    def _start_v4l2(self, dev):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            return False

        # zníž buffre, vypni RGB konverziu
        try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception: pass
        try: cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        except Exception: pass

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera.height)
        cap.set(cv2.CAP_PROP_FPS, self.camera.fps)

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
        self.camera._logger.debug("backend mode selected=%s device=%s", self._mode, dev)
        self.camera._logger.debug("pipeline open backend=%s device=%s", self._mode, dev)
        self._stop.clear()
        self._t = threading.Thread(target=self._grab_loop, daemon=True)
        self._t.start()
        print(f"[Camera] V4L2 started on {dev} {self.camera.width}x{self.camera.height}@{self.camera.fps} {self.camera.pixel_format}")
        return True


    def _grab_loop(self):
        while not self._stop.is_set():
            if self.camera._is_trigger_capture_active():
                time.sleep(0.002)
                continue
            with self._cap_read_lock:
                if self._cap is None:
                    time.sleep(0.005)
                    continue
                acquired_at = time.monotonic()
                ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.005)
                continue

            frame = self._normalize_frame_u8(frame)
            if frame is None:
                continue

            self._publish_master_frame(frame, acquired_at - max(
                1.0 / max(1, self.camera.fps), self.camera.exposure_us / 1_000_000.0
            ))

            try:
                if self._q.full():
                    _ = self._q.get_nowait()
                self._q.put_nowait(frame)
            except queue.Full:
                pass


    def start(self, *, caller: str = "unspecified"):
        self.camera.resolve_device()
        requested_signature = self._pipeline_signature()
        if self.is_pipeline_open():
            if self._active_pipeline_signature == requested_signature:
                self._log_reuse_existing_pipeline(caller)
                return False
            self.stop(caller=f"{caller}:restart")

        self._log_start_pipeline(requested_signature, caller)
        # poskladaj kandidátov tak, aby bol self.camera.device prvý a bez duplicít
        seen = set()
        devs = []
        for d in [self.camera.device] + list(self.camera.devices):
            if d and d not in seen:
                devs.append(d); seen.add(d)

        if self.camera.pio._pio_configuring or self.camera.pio._pio_ready is not None:
            devs = [self.camera.device]
        # 1) GStreamer
        if _GST_OK:
            for dev in devs:
                if self._start_gst(dev):
                    self.camera.device = dev
                    self._active_pipeline_signature = self._pipeline_signature(device=dev)
                    self.camera._hid = None
                    self.camera._init_hid()
                    self.camera._last_open_args.update({
                        "device": dev,
                        "width": int(self.camera.width),
                        "height": int(self.camera.height),
                        "fps": int(self.camera.fps),
                        "fourcc": "GREY",
                        "pixel_format": self.camera.pixel_format,
                    })
                    self.camera.get_supported_v4l2_controls(refresh=True)
                    return True

        if self.camera.pio._pio_configuring:
            raise RuntimeError("PIO GStreamer stream sa nepodarilo otvoriť.")
        # 2) Fallback: OpenCV V4L2
        for dev in devs:
            if self._start_v4l2(dev):
                self.camera.device = dev
                self._active_pipeline_signature = self._pipeline_signature(device=dev)
                self.camera._hid = None
                self.camera._init_hid()
                self.camera._last_open_args.update({
                    "device": dev,
                    "width": int(self.camera.width),
                    "height": int(self.camera.height),
                    "fps": int(self.camera.fps),
                    "fourcc": "GREY",
                    "pixel_format": self.camera.pixel_format,
                })
                self.camera.get_supported_v4l2_controls(refresh=True)
                return True

        # nič sa neotvorilo
        raise RuntimeError("Camera open failed (V4L2 and GStreamer). Check /dev/video* and formats.")


    def one_shot(self):
        """Legacy/fallback snapshot z preview queue/appsink path."""
        self.camera._logger.debug("one_shot() is legacy preview fallback (queue/appsink)")
        if not self.camera._is_preview_path_active():
            self.camera._logger.debug("one_shot requested while preview path is inactive")
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


    def stop(self, *, caller: str = "unspecified", expected_bus=None):
        with self._delivery_lock:
            if expected_bus is not None and expected_bus is not self._bus:
                return
            bus, self._bus = self._bus, None
            self._sink = None
            self.camera.pio._invalidate_pio()
        if bus is not None:
            bus.remove_signal_watch()
        self._log_stop_pipeline(caller)
        self._stop.set()
        self.camera._logger.debug("pipeline close requested mode=%s", self._mode)
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
        if self._t is not None and self._t.is_alive() and self._t is not threading.current_thread():
            self._t.join(timeout=1.0)
        self._t = None
        self._mode = None
        self.camera._logger.debug("pipeline closed")
        self._active_pipeline_signature = None
        self.camera._trigger_primed = False
        self.camera._trigger_priming_in_progress = False
        self.camera.end_trigger_capture()
        if self.camera._hid is not None:
            try:
                self.camera._hid.close()
            except Exception:
                pass
            self.camera._hid = None
        self._reset_buffers()
        self.stop_continuous()


    def start_continuous(self):
        """Spustí ľahký kontinuálny zber do ring bufferu (bez GStreamer UI)."""
        if self._t_ring and self._t_ring.is_alive():
            self.camera._logger.debug("start_continuous skipped: ring loop already running")
            return
        self.camera._logger.info("start_continuous activated (ring capture is preview-side helper, not trigger path)")
        self._stop_ring.clear()
        self._t_ring = threading.Thread(target=self._loop_ring, daemon=True)
        self._t_ring.start()


    def _loop_ring(self):
        import time
        while not self._stop_ring.is_set():
            if self._cap is None:
                time.sleep(0.01)
                continue
            if self.camera._is_trigger_capture_active():
                self.camera._logger.debug("ring capture paused: trigger capture active")
                time.sleep(0.002)
                continue
            with self._cap_read_lock:
                if self._cap is None:
                    time.sleep(0.01)
                    continue
                ok, frame = self._cap.read()
            if not ok or frame is None:
                time.sleep(0.002); continue
            frame = self._normalize_frame_u8(frame)
            if frame is None:
                continue
            self._ring.append(frame)


    def last_frame(self, *, caller: str = "unspecified"):
        """Vráti posledný frame z kontinuálneho zberu, inak spraví rýchly oneshot ako fallback."""
        if self.camera._is_trigger_path_active() and self.camera._is_trigger_capture_active():
            self.camera._logger.debug(
                "last_frame fallback blocked: trigger capture flow active (caller=%s)",
                caller,
            )
            if self._ring:
                self._log_latest_frame_used(caller)
                return self._ring[-1]
            raise RuntimeError("last_frame unavailable during active trigger capture flow")

        if self.camera._is_trigger_path_active():
            self.camera._logger.debug("last_frame in trigger mode: one_shot legacy fallback disabled (caller=%s)", caller)
        if self._ring:
            self._log_latest_frame_used(caller)
            return self._ring[-1]
        if self.camera._is_trigger_path_active():
            raise RuntimeError("No ring frame available in trigger mode")
        self._log_latest_frame_used(caller)
        return self.one_shot()


    def discard_frames(self, count: int = 3, *, caller: str = "") -> int:
        """Best-effort flush starších frame-ov pred finálnym odberom v master flow."""
        discard_count = max(0, int(count))
        if discard_count <= 0:
            self.camera._logger.debug("[FRAME_FLUSH] skipped reason=non_positive_count count=%s caller=%s", count, caller)
            return 0
        if self._cap is None:
            self.camera._logger.debug("[FRAME_FLUSH] skipped reason=capture_not_ready caller=%s", caller)
            return 0
        if self.camera._is_trigger_path_active():
            self.camera._logger.debug("[FRAME_FLUSH] skipped because capture_mode=trigger caller=%s", caller)
            return 0

        discarded = 0
        try:
            with self._cap_read_lock:
                cap = self._cap
                if cap is None:
                    self.camera._logger.debug("[FRAME_FLUSH] skipped reason=capture_not_ready caller=%s", caller)
                    return 0
                for _ in range(discard_count):
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    frame_u8 = self._normalize_frame_u8(frame)
                    if frame_u8 is None:
                        continue
                    self._ring.append(frame_u8)
                    discarded += 1
        except Exception as exc:
            self.camera._logger.warning("[FRAME_FLUSH] failed reason=%s caller=%s", exc, caller)
            return discarded

        self.camera._logger.debug(
            "[FRAME_FLUSH] mode=master discard_count=%s discarded=%s caller=%s",
            discard_count,
            discarded,
            caller,
        )
        return discarded


    def stop_continuous(self):
        self.camera._logger.info("stop_continuous requested")
        try:
            self._stop_ring.set()
        except Exception:
            pass


    def _clear_queue(self):
        cleared = 0
        while True:
            try:
                self._q.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        self.camera._logger.debug("queue clear done dropped=%s", cleared)


    def _clear_ring(self):
        cleared = len(self._ring)
        self._ring.clear()
        self.camera._logger.debug("ring clear done dropped=%s", cleared)



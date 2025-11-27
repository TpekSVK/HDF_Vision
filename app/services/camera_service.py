# app/services/camera_service.py
import os
import cv2
import numpy as np
import threading
import time
import queue
from collections import deque

from app.services.xu_stub import XUControls

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
        self._gst_context = None

        # zásobník kandidátov zariadení
        self.devices = [self.device, "/dev/video0", "/dev/video1"]

        self._ring = deque(maxlen=5)
        self._t_ring = None
        self._stop_ring = threading.Event()
        self._paused_external = False
        self._last_open_args = {"device": self.devices[0] if self.devices else "/dev/video0",
                                "width": 1920, "height": 1080, "fps": 60, "fourcc": "GREY",
                                "pixel_format": self.pixel_format}
        self._xu: XUControls | None = None

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

    def _recover_from_empty_queue(self):
        """Best-effort recovery when capture produces no frames.

        When the underlying pipeline stalls (for example because the device is
        busy) the queue may never receive a frame. Shutting down the active
        backend releases the device handle so a subsequent ``start`` call can
        retry cleanly instead of leaving a PLAYING pipeline behind.
        """

        try:
            self.stop()
        except Exception:
            # Recovery should never raise – callers only care about releasing
            # the device and clearing stale buffers so they can retry.
            pass

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
            GLib.idle_add(self._gst_teardown_from_loop)
        elif t == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            print(f"[GST][WARN] {err} debug:{dbg}")
        elif t == Gst.MessageType.EOS:
            print("[GST] EOS")
            GLib.idle_add(self._gst_teardown_from_loop)

    def _gst_teardown_from_loop(self):
        # Called inside the GLib main context; avoid joining the loop thread from itself.
        self._stop.set()
        self._shutdown_gst(from_loop=True)
        self._mode = None
        self._reset_buffers()
        self.stop_continuous()
        return False

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

            context = GLib.MainContext()

            # zaregistruj bus signál watch v samostatnom GLib kontexte, aby sa
            # nehádal s Qt event loop-om alebo inými GLib slučkami
            bus = pipeline.get_bus()
            context.push_thread_default()
            try:
                bus.add_signal_watch()
                bus.connect("message", self._gst_bus_cb)
            finally:
                context.pop_thread_default()

            loop = GLib.MainLoop(context=context)

            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                tried.append(("PLAYING_fail", "", pipe))
                pipeline.set_state(Gst.State.NULL)
                continue

            # uisti sa, že pipeline naozaj prešla do PLAYING (napr. ak je /dev/video* busy)
            change_ret, state, _pending = pipeline.get_state(2 * Gst.SECOND)
            if change_ret == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
                err_msg = ""
                msg = bus.pop_filtered(Gst.MessageType.ERROR)
                if msg is not None:
                    err, _dbg = msg.parse_error()
                    err_msg = str(err)
                tried.append(("PLAYING_fail", err_msg, pipe))
                pipeline.set_state(Gst.State.NULL)
                continue

            # uložiť runtime objekty a spustiť loop v thread-e
            self._pipeline = pipeline
            self._loop = loop
            self._gst_context = context
            self._mode = "gst"
            self._stop.clear()

            def _loop_run():
                try:
                    context.push_thread_default()
                    try:
                        loop.run()
                    finally:
                        context.pop_thread_default()
                except Exception as e:
                    print("[GST] MainLoop exception:", e)

            self._t = threading.Thread(target=_loop_run, daemon=True)
            self._t.start()
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
    def start(self):
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
                    self._xu = None
                    self._last_open_args.update({
                        "device": dev,
                        "width": int(self.width),
                        "height": int(self.height),
                        "fps": int(self.fps),
                        "fourcc": "GREY",
                        "pixel_format": self.pixel_format,
                    })
                    return

        # 2) Fallback: OpenCV V4L2
        for dev in devs:
            if self._start_v4l2(dev):
                self.device = dev
                self._xu = None
                self._last_open_args.update({
                    "device": dev,
                    "width": int(self.width),
                    "height": int(self.height),
                    "fps": int(self.fps),
                    "fourcc": "GREY",
                    "pixel_format": self.pixel_format,
                })
                return

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
            self._recover_from_empty_queue()
            raise RuntimeError("No frame available for one-shot.")
        return last

    def _shutdown_gst(self, *, from_loop: bool = False):
        """Tear down the GStreamer pipeline safely.

        GLib ``MainLoop.quit`` must run in the same context that owns the loop;
        calling it from another thread can sporadically crash the interpreter
        (observed as a segmentation fault when toggling Modbus in the UI).  When
        we're outside the loop thread, schedule the teardown via
        ``GLib.idle_add`` on the loop's context and wait briefly for it to
        complete.
        """

        def _do_teardown():
            try:
                if self._pipeline is not None:
                    try:
                        self._pipeline.set_state(Gst.State.NULL)
                    except Exception:
                        pass
            finally:
                try:
                    if self._loop is not None:
                        self._loop.quit()
                except Exception:
                    pass
                return False  # stop idle handler

        if from_loop:
            _do_teardown()
        else:
            # Run teardown inside the GLib context to avoid cross-thread calls.
            quit_done = threading.Event()

            def _wrapper():
                try:
                    _do_teardown()
                finally:
                    quit_done.set()
                return False

            try:
                if self._gst_context is not None:
                    GLib.idle_add(_wrapper, context=self._gst_context)
                else:
                    GLib.idle_add(_wrapper)
                quit_done.wait(timeout=1.0)
            except Exception:
                pass

        self._pipeline = None
        self._loop = None
        self._gst_context = None

        if not from_loop and self._t is not None and self._t.is_alive():
            self._t.join(timeout=1.0)
        self._t = None

    def stop(self):
        self._stop.set()
        # V4L2
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        # GST
        self._shutdown_gst(from_loop=False)
        self._mode = None
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

    def last_frame(self):
        """Vráti posledný frame z kontinuálneho zberu, inak spraví rýchly oneshot ako fallback."""
        if self._ring:
            return self._ring[-1]
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
        self.stop()
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
        self._xu = None
        self.start()
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
        if was_running:
            self.stop()
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
                self.start()
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
                    self.start()
                except Exception:
                    pass
                raise RuntimeError(f"Camera reopen failed: {exc}") from exc

    def _ensure_xu(self) -> XUControls:
        if self._xu is None or getattr(self._xu, "video_dev", None) != self.device:
            self._xu = XUControls(self.device)
        return self._xu

    def set_manual_exposure_us(self, exposure_us: int):
        val = int(exposure_us)
        try:
            self._ensure_xu().set_manual_exposure_us(val)
        except Exception as exc:
            raise RuntimeError(f"Set exposure failed: {exc}") from exc
        self.exposure_us = val

    def set_gain_db(self, gain_db: int):
        val = int(gain_db)
        try:
            self._ensure_xu().set_gain_db(val)
        except Exception as exc:
            raise RuntimeError(f"Set gain failed: {exc}") from exc
        self.gain_db = val

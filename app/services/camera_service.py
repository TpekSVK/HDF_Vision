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
            raise RuntimeError("No frame available for one-shot.")
        return last

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


    def select_device(self, device: str):
        target = str(device or "").strip()
        if not target:
            return
        if target == self.device:
            return

        current = self.device
        was_running = any([self._cap is not None, self._pipeline is not None, self._mode])
        if was_running:
            self.stop()

        self.device = target
        known = [target] + [d for d in self.devices if d != target]
        self.devices = known
        self._last_open_args.update({"device": target})

        if was_running:
            try:
                self.start()
            except Exception as exc:
                self.device = current
                self.devices = [current] + [d for d in self.devices if d != current]
                self._last_open_args.update({"device": current})
                try:
                    self.start()
                except Exception:
                    pass
                raise RuntimeError(f"Camera switch to {target} failed: {exc}") from exc

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

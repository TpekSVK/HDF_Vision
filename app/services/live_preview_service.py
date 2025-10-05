# app/services/live_preview_service.py
import os
import threading
import queue
import numpy as np

# GStreamer
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)


class LivePreviewService:
    """
    Ľahký LIVE náhľad cez GStreamer (v4l2src -> GRAY8 -> appsink).
    - Žiadna konverzia, rovno GRAY8 caps (ako reportuje tvoja kamera).
    - Thread s GLib.MainLoop.
    - last_frame_u8() vráti posledný frame (uint8 HxW).
    """
    def __init__(self, device: str, width: int = 1280, height: int = 720, fps: int = 60):
        self.device = device or os.getenv("CAM_DEV", "/dev/video0")
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)

        self._pipeline = None
        self._loop = None
        self._th = None
        self._q = queue.Queue(maxsize=2)
        self._running = False

    def _pipe_str(self) -> str:
        # Preferuj priame caps (GRAY8). Ak by niekde zlyhávala negociácia,
        # je možné doplniť `videoconvert !` pred caps.
        caps = f"video/x-raw,format=GRAY8,width={self.width},height={self.height},framerate={self.fps}/1"
        return (
            f"v4l2src device={self.device} io-mode=2 ! "
            f"{caps} ! "
            f"appsink name=sink emit-signals=true sync=false drop=true max-buffers=2"
        )

    # --- appsink callbacks ---
    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)  # ← FIX: enum, nie int
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = int(s.get_value("width"))
            h = int(s.get_value("height"))
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
            # appsink môže dodať stride > width, preto re-shape a orezať
            arr = arr.reshape((h, -1))[:, :w].copy()
            if self._q.full():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
            self._q.put_nowait(arr)
        finally:
            buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def _on_bus(self, bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"[Live][GST][ERROR] {err} debug:{dbg}")
        elif t == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            print(f"[Live][GST][WARN] {err} debug:{dbg}")
        elif t == Gst.MessageType.EOS:
            print("[Live][GST] EOS")
            self.stop()

    def start(self):
        if self._running:
            return
        pipe = self._pipe_str()
        try:
            pipeline = Gst.parse_launch(pipe)
        except Exception as e:
            raise RuntimeError(f"Live pipeline parse failed: {e}")

        sink = pipeline.get_by_name("sink")
        if sink is None:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("appsink 'sink' not found")

        sink.connect("new-sample", self._on_sample)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)

        loop = GLib.MainLoop()

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Live pipeline PLAYING failed")

        self._pipeline = pipeline
        self._loop = loop
        self._running = True

        def _run():
            try:
                loop.run()
            except Exception as e:
                print("[Live] MainLoop exception:", e)

        self._th = threading.Thread(target=_run, daemon=True)
        self._th.start()
        print(f"[Live] started: {pipe}")

    def stop(self):
        if not self._running:
            return
        try:
            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        try:
            if self._loop is not None:
                self._loop.quit()
        except Exception:
            pass
        if self._th and self._th.is_alive():
            try:
                self._th.join(timeout=1.0)
            except Exception:
                pass
        self._pipeline = None
        self._loop = None
        self._th = None
        self._running = False
        print("[Live] stopped")

    def last_frame_u8(self):
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

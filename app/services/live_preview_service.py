# app/services/live_preview_service.py
import threading, time
import numpy as np

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib
    Gst.init(None)
    _GST_OK = True
except Exception:
    _GST_OK = False

class LivePreviewService:
    """
    Ľahký live náhľad (SETUP): v4l2src -> GRAY8 -> appsink; číta posledný frame.
    Použitie:
      lp = LivePreviewService("/dev/video0", 1280, 720, 60)
      lp.start();  img = lp.last_frame_u8();  lp.stop()
    """
    def __init__(self, device="/dev/video0", width=1280, height=720, fps=60):
        self.device = device
        self.width = int(width); self.height = int(height); self.fps = int(fps)
        self._loop = None
        self._pipeline = None
        self._sink = None
        self._thread = None
        self._stop = threading.Event()
        self._last = None

    def _pipeline_str(self):
        caps = f"video/x-raw,format=GRAY8,width={self.width},height={self.height},framerate={self.fps}/1"
        return (
            f"v4l2src device={self.device} io-mode=2 ! {caps} ! "
            f"videoconvert ! appsink name=sink emit-signals=true sync=false drop=true max-buffers=1"
        )

    def start(self):
        if not _GST_OK:
            raise RuntimeError("GStreamer nie je k dispozícii v konte.")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        from gi.repository import Gst, GLib
        self._loop = GLib.MainLoop()
        self._pipeline = Gst.parse_launch(self._pipeline_str())
        self._sink = self._pipeline.get_by_name("sink")
        self._sink.connect("new-sample", self._on_sample)
        self._pipeline.set_state(Gst.State.PLAYING)
        try:
            self._loop.run()
        finally:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._sink = None

    def _on_sample(self, sink):
        if self._stop.is_set():
            return Gst.FlowReturn.EOS
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        caps = sample.get_caps()
        w = caps.get_structure(0).get_value("width")
        h = caps.get_structure(0).get_value("height")
        success, mapinfo = buf.map(0x1)  # READ
        if success:
            try:
                # GRAY8 - lineárny buffer
                arr = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape(h, w).copy()
                self._last = arr
            finally:
                buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def last_frame_u8(self):
        return self._last

    def stop(self):
        if self._loop:
            self._stop.set()
            try:
                self._loop.quit()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._loop = None

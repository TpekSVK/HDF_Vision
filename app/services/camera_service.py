import threading, queue, time
import numpy as np

# GStreamer (gst-python)
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)

class CameraService:
    def __init__(self, device="/dev/video0", width=1920, height=1080, fps=60):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self._q = queue.Queue(maxsize=5)
        self._stop = threading.Event()
        self._thread = None
        self._pipeline = None
        self._loop = None
        self._sink = None

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        success, map_info = buf.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            # GREY 8-bit -> numpy (H x W)
            arr = np.frombuffer(map_info.data, dtype=np.uint8)
            # GStreamer non-interleaved gray je plocha height*stride; zoberieme šírku podľa caps
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = s.get_value("width"); h = s.get_value("height")
            # stride môže byť väčší, ale pre GREY z v4l2src je zvyčajne == width
            arr = arr.reshape((h, -1))[:, :w].copy()
            if self._q.full():
                try: self._q.get_nowait()
                except queue.Empty: pass
            self._q.put_nowait(arr)
        finally:
            buf.unmap(map_info)
        return Gst.FlowReturn.OK

    def _gst_pipeline_str(self, dev, use_convert=False):
        # prefer GRAY8; ak use_convert=True, vložíme videoconvert pre robustnosť
        caps = f"video/x-raw,format=GRAY8,width={self.width},height={self.height},framerate={self.fps}/1"
        if use_convert:
            return f"v4l2src device={dev} ! videoconvert ! {caps} ! appsink name=sink emit-signals=true sync=false drop=true max-buffers=2"
        else:
            return f"v4l2src device={dev} ! {caps} ! appsink name=sink emit-signals=true sync=false drop=true max-buffers=2"
        

    def _bus_cb(self, bus, msg):
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

    def start(self):
        tried = []
        for dev in [self.device, "/dev/video0", "/dev/video1"]:
            for use_convert in [False, True]:
                try:
                    pipe = Gst.parse_launch(self._gst_pipeline_str(dev, use_convert=use_convert))
                except Exception as e:
                    tried.append((dev, use_convert, f"parse_fail:{e}"))
                    continue

                sink = pipe.get_by_name("sink")
                if sink is None:
                    tried.append((dev, use_convert, "no_sink"))
                    continue
                sink.connect("new-sample", self._on_new_sample)

                bus = pipe.get_bus()
                bus.add_signal_watch()
                bus.connect("message", self._bus_cb)

                self._loop = GLib.MainLoop()
                def _loop_run():
                    try: self._loop.run()
                    except Exception as e: print("[GST] MainLoop exception:", e)

                self._pipeline = pipe
                self._sink = sink

                ret = self._pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    tried.append((dev, use_convert, "PLAYING_fail"))
                    self._pipeline.set_state(Gst.State.NULL)
                    self._pipeline = None
                    continue

                self._stop.clear()
                self._thread = threading.Thread(target=_loop_run, daemon=True)
                self._thread.start()
                mode = "GRAY8" + ("+videoconvert" if use_convert else "")
                print(f"[Camera] Started via GStreamer on {dev} {self.width}x{self.height}@{self.fps} {mode}")
                return

        raise RuntimeError(f"GStreamer camera open failed. Tried: {tried}")

    def one_shot(self):
        # vráti posledný dostupný frame
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
        try:
            if self._pipeline:
                self._pipeline.set_state(Gst.State.NULL)
        finally:
            if self._loop:
                try: self._loop.quit()
                except Exception: pass
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._pipeline = None
            self._loop = None
            self._thread = None

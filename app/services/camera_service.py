import os
import cv2
import numpy as np
import threading, queue, time

# Voliteľné: GStreamer fallback (nebude vyžadovaný, ak nechceš)
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
    V4L2-first capture pre mono (GREY/Y12/Y16) kamery:
      1) Skús /dev/videoX priamo cez cv2.CAP_V4L2 (explicitný FourCC, W/H/FPS, CONVERT_RGB=0)
      2) Ak neprejde, fallback na GStreamer: v4l2src ! GRAY8 ! appsink
    Frames sa dávajú do queue a čítajú v konzumentovi (UI thread).
    """

    def __init__(self, device="/dev/video0", width=1280, height=720, fps=60, try_devices=("/dev/video0", "/dev/video1")):
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.devices = [device] + [d for d in try_devices if d != device and os.path.exists(d)]
        self.cap = None
        self._mode = None  # "v4l2" alebo "gst"
        self._stop = threading.Event()
        self._q = queue.Queue(maxsize=5)
        self._t = None

        # GST members (len keď fallbackujeme)
        self._pipeline = None
        self._loop = None
        self._sink = None

    # ----------------- V4L2 priame otvorenie -----------------

    def _open_v4l2(self, dev):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            return None

        # Mono (GREY8) — skúsiť Y800; niektoré kamery môžu potrebovať "Y16 "
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"Y800"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        try:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        except Exception:
            pass

        ok, frame = cap.read()
        if not ok or frame is None:
            # Skús ešte Y16 (ak kamera reportuje Y12/16)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"Y16 "))
            ok2, frame2 = cap.read()
            if not ok2 or frame2 is None:
                cap.release()
                return None

        return cap

    def _loop_v4l2(self):
        # Snímame a tlačíme do queue; frame môže byť 2D (GRAY) alebo 16-bit
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok or frame is None:
                time.sleep(0.005)
                continue

            # Pri Y16 môže OpenCV vrátiť 16-bit single channel (shape HxW) alebo HxWx2/3; normalizácia:
            if frame.ndim == 3 and frame.shape[2] == 1:
                frame = frame[:, :, 0]

            if self._q.full():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
            self._q.put_nowait(frame)

    # ----------------- GStreamer fallback -----------------

    def _gst_pipeline_str(self, dev, fmt="GRAY8"):
        caps = f"video/x-raw,format={fmt},width={self.width},height={self.height},framerate={self.fps}/1"
        # videoconvert pred appsink pre robustnosť medzi formátmi
        return (
            f"v4l2src device={dev} ! {caps} ! "
            f"videoconvert ! appsink name=sink emit-signals=true sync=false drop=true max-buffers=2"
        )

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return 1
        buf = sample.get_buffer()
        success, map_info = buf.map(1)  # READ
        if not success:
            return 1
        try:
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = int(s.get_value("width"))
            h = int(s.get_value("height"))
            # predpoklad GRAY8
            arr = np.frombuffer(map_info.data, dtype=np.uint8)
            arr = arr.reshape((h, -1))[:, :w].copy()
            if self._q.full():
                try: self._q.get_nowait()
                except queue.Empty: pass
            self._q.put_nowait(arr)
        finally:
            buf.unmap(map_info)
        return 0

    def _start_gst(self, dev):
        if not _GST_OK:
            return False
        try:
            pipe = self._gst_pipeline_str(dev, "GRAY8")
            pipeline = Gst.parse_launch(pipe)
        except Exception:
            return False

        sink = pipeline.get_by_name("sink")
        if sink is None:
            return False
        sink.connect("new-sample", self._on_new_sample)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._bus_cb)

        self._loop = GLib.MainLoop()
        def _run():
            try:
                self._loop.run()
            except Exception as e:
                print("[GST] loop err:", e)

        self._pipeline = pipeline
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._loop = None
            return False

        self._mode = "gst"
        self._t = threading.Thread(target=_run, daemon=True)
        self._t.start()
        print(f"[Camera] GST started on {dev} {self.width}x{self.height}@{self.fps} GRAY8")
        return True

    def _bus_cb(self, bus, msg):
        t = msg.type
        if t == getattr(Gst, "MessageType").ERROR:
            err, dbg = msg.parse_error()
            print(f"[GST][ERROR] {err} debug:{dbg}")
        elif t == getattr(Gst, "MessageType").WARNING:
            err, dbg = msg.parse_warning()
            print(f"[GST][WARN] {err} debug:{dbg}")
        elif t == getattr(Gst, "MessageType").EOS:
            print("[GST] EOS")
            self.stop()

    # ----------------- Public API -----------------

    def start(self):
        # 1) V4L2 priamo
        for dev in self.devices:
            if not os.path.exists(dev):
                continue
            cap = self._open_v4l2(dev)
            if cap is not None:
                self.cap = cap
                self._mode = "v4l2"
                self._stop.clear()
                self._t = threading.Thread(target=self._loop_v4l2, daemon=True)
                self._t.start()
                print(f"[Camera] V4L2 started on {dev} {self.width}x{self.height}@{self.fps}")
                return

        # 2) Fallback: GStreamer
        for dev in self.devices:
            if self._start_gst(dev):
                return

        raise RuntimeError("Camera open failed (V4L2 and GStreamer). Check /dev/video* and formats.")

    def one_shot(self, timeout=0.8):
        # vráti posledný dostupný frame
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
        if last is None:
            raise RuntimeError("No frame available.")
        return last

    def stop(self):
        self._stop.set()
        try:
            if self._mode == "v4l2" and self.cap:
                self.cap.release()
            elif self._mode == "gst" and self._pipeline:
                self._pipeline.set_state(Gst.State.NULL)
                if self._loop:
                    try: self._loop.quit()
                    except Exception: pass
        finally:
            if self._t and self._t.is_alive():
                self._t.join(timeout=1.0)
        self.cap = None
        self._pipeline = None
        self._loop = None
        self._t = None
        self._mode = None

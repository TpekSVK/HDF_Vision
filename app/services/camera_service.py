# --- REPLACE whole class with this improved version ---
import threading, time, queue
import cv2
import numpy as np
import os

class CameraService:
    def __init__(self, device="/dev/video0", width=1920, height=1080, fps=60, pixel_format="GRAY8"):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.pixel_format = pixel_format  # "GRAY8"
        self._cap = None
        self._q = queue.Queue(maxsize=5)
        self._stop = threading.Event()
        self._thread = None
        self._backend = None  # "GST" alebo "V4L2"

    def _gst_pipeline_variants(self):
        # Niektoré buildy očakávajú GREY namiesto GRAY8, niektorým vadí io-mode
        base = f"v4l2src device={self.device}"
        caps1 = f"video/x-raw,format=GRAY8,width={self.width},height={self.height},framerate={self.fps}/1"
        caps2 = f"video/x-raw,format=GREY,width={self.width},height={self.height},framerate={self.fps}/1"
        variants = [
            f"{base} ! {caps1} ! appsink drop=true sync=false max-buffers=2",
            f"{base} io-mode=2 ! {caps1} ! appsink drop=true sync=false max-buffers=2",
            f"{base} ! {caps2} ! appsink drop=true sync=false max-buffers=2",
            f"{base} ! {caps1} ! videoconvert ! appsink drop=true sync=false max-buffers=2",
        ]
        return variants

# --- nahrad funkciu start() a pridaj helper _try_open_v4l2() ---
    def _try_open_v4l2(self, dev):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if cap.isOpened():
            try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            except Exception: pass
            try: cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            except Exception: pass
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            try:
                fourcc_grey = cv2.VideoWriter_fourcc(*"GREY")
                if not cap.set(cv2.CAP_PROP_FOURCC, fourcc_grey):
                    fourcc_y800 = cv2.VideoWriter_fourcc(*"Y800")
                    cap.set(cv2.CAP_PROP_FOURCC, fourcc_y800)
            except Exception:
                pass
            if cap.isOpened():
                print(f"[Camera] V4L2 open OK -> {dev}")
                return cap
        return None

    def start(self):
        # 0) GStreamer (ak by bol dostupný — u teba nie je, logy to ukázali)
        for pipe in self._gst_pipeline_variants():
            cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
            if cap.isOpened():
                self._cap = cap; self._backend = "GST"; break

        # 1) Ak GST nevyšiel, skús V4L2 cez viac kandidátov
        if self._cap is None:
            candidates = []
            # ak bol zadaný string (path), skús najprv ten
            if isinstance(self.device, str):
                candidates.append(self.device)
            # bežné cesty
            candidates += ["/dev/video0", "/dev/video1", "/dev/video2"]
            # indexy (niektoré buildy lepšie fungujú s indexom)
            candidates += [0, 1, 2]

            for dev in candidates:
                cap = self._try_open_v4l2(dev)
                if cap:
                    self._cap = cap; self._backend = "V4L2"; break

        if not (self._cap and self._cap.isOpened()):
            raise RuntimeError("Camera open failed (GST & V4L2). Skontroluj /dev/video* a formáty.")

        self._stop.clear()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()
        print(f"[Camera] Started via backend: {self._backend}")


        # 2) Fallback: CAP_V4L2
        if self._cap is None:
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            if cap.isOpened():
                # znížime ring buffer (ak driver podporuje)
                try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                except Exception: pass
                # zakážeme RGB konverziu
                try: cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
                except Exception: pass
                # rozlíšenie / fps
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, self.fps)
                # preferuj GREY/Y800
                try:
                    # niekedy je stabilnejšie GREY ako Y800
                    fourcc_grey = cv2.VideoWriter_fourcc(*"GREY")
                    if not cap.set(cv2.CAP_PROP_FOURCC, fourcc_grey):
                        fourcc_y800 = cv2.VideoWriter_fourcc(*"Y800")
                        cap.set(cv2.CAP_PROP_FOURCC, fourcc_y800)
                except Exception:
                    pass


        if not (self._cap and self._cap.isOpened()):
            raise RuntimeError("Camera open failed (GST & V4L2). Skontroluj /dev/video* a formáty.")

        self._stop.clear()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()
        print(f"[Camera] Started via backend: {self._backend}")

    def _grab_loop(self):
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.005)
                continue
            # ak by prišiel BGR (niektoré V4L2 buildy), prekonvertuj na grey
            if frame.ndim == 3 and frame.shape[2] == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            try:
                if self._q.full():
                    _ = self._q.get_nowait()
                self._q.put_nowait(frame)
            except queue.Full:
                pass

    def one_shot(self):
        tries = 0
        last = None
        while tries < 3:
            try:
                last = self._q.get(timeout=0.2)
            except queue.Empty:
                tries += 1
                continue
            tries += 1
        if last is None:
            raise RuntimeError("No frame available for one-shot.")
        return last

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
        self._thread = None
        self._cap = None

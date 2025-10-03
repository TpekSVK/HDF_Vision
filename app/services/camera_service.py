import threading, time, queue
import cv2
import numpy as np

class CameraService:
    def __init__(self, device="/dev/video0", width=1920, height=1080, fps=60, pixel_format="GRAY8"):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.pixel_format = pixel_format
        self._cap = None
        self._q = queue.Queue(maxsize=5)
        self._stop = threading.Event()
        self._thread = None

    def _gst_pipeline(self) -> str:
        # UVC -> Y8 (GRAY8), bez HW konverzie; appsink pre OpenCV
        return (
            f"v4l2src device={self.device} io-mode=2 ! "
            f"video/x-raw,format={self.pixel_format},width={self.width},height={self.height},framerate={self.fps}/1 ! "
            f"appsink drop=true sync=false max-buffers=2"
        )

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._cap = cv2.VideoCapture(self._gst_pipeline(), cv2.CAP_GSTREAMER)
        if not self._cap.isOpened():
            raise RuntimeError("Camera open failed. Check /dev/video* and permissions.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def _grab_loop(self):
        # beží ticho na pozadí; ponechávame posledné stabilné frame-y
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.005)
                continue
            # GRAY8 -> cv2 je už 8-bit single channel
            try:
                if self._q.full():
                    _ = self._q.get_nowait()
                self._q.put_nowait(frame)
            except queue.Full:
                pass

    def one_shot(self):
        # vráti posledný stabilný snímok
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

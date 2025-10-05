# app/services/live_preview_service.py
from __future__ import annotations
import os
import threading
import queue
import time
from typing import Optional, Tuple

import numpy as np

# GStreamer
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

# OpenCV (pre voliteľný CUDA upload posledného snímku)
import cv2

Gst.init(None)


def _has_cuda() -> bool:
    return hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0


class LivePreviewService:
    """
    Ľahký LIVE náhľad cez GStreamer (v4l2src -> GRAY8 -> appsink).

    Vlastnosti:
      - Negotiácia priamo do GRAY8 caps (žiadne konverzie), voliteľne s fallbackom cez videoconvert.
      - Thread s GLib.MainLoop + message bus (ERROR/WARNING/EOS).
      - Last-frame queue (O(1) výmena posledného snímku).
      - last_frame_u8() -> numpy uint8 HxW.
      - last_frame_gpu() -> cv2.cuda_GpuMat (ak je CUDA), inak None.
      - last_meta() -> (timestamp_ns, width, height, fps).

    Pozn.: appsink dodáva aj stride > width, re-shape je ošetrený.
    """

    def __init__(
        self,
        device: str,
        width: int = 1280,
        height: int = 720,
        fps: int = 60,
        use_videoconvert_fallback: bool = False,
    ):
        self.device = device or os.getenv("CAM_DEV", "/dev/video0")
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.use_videoconvert_fallback = bool(use_videoconvert_fallback)

        self._pipeline: Optional[Gst.Pipeline] = None
        self._loop: Optional[GLib.MainLoop] = None
        self._th: Optional[threading.Thread] = None
        self._running = False

        # posledné dáta (1-slot buffer)
        self._q = queue.Queue(maxsize=1)          # np.ndarray (HxW, uint8)
        self._q_meta = queue.Queue(maxsize=1)     # (timestamp_ns, w, h)
        self._last_gpu: Optional[cv2.cuda_GpuMat] = None if _has_cuda() else None
        self._gpu_lock = threading.Lock()

        # cache na pipeline string pre debug
        self._last_pipe_str: Optional[str] = None

    # ---- Pipeline builder ----
    def _pipe_str(self) -> str:
        """
        Preferuj priame caps (GRAY8). Ak by niekde zlyhávala negociácia,
        dá sa zapnúť fallback `videoconvert`.
        """
        caps = f"video/x-raw,format=GRAY8,width={self.width},height={self.height},framerate={self.fps}/1"

        # io-mode=2 (mmap) je bežne stabilné; prípadne 4 (dmabuf) podľa kamery.
        base = f"v4l2src device={self.device} io-mode=2"

        convert = "videoconvert ! " if self.use_videoconvert_fallback else ""

        sink = (
            "appsink name=sink emit-signals=true sync=false "
            "drop=true max-buffers=2"
        )

        pipe = f"{base} ! {convert}{caps} ! {sink}"
        self._last_pipe_str = pipe
        return pipe

    # ---- appsink callbacks ----
    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR

        try:
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = int(s.get_value("width"))
            h = int(s.get_value("height"))

            # timestamp (nanosekundy); ak chýba, doplníme monotónnym časom
            ts = buf.pts if buf.pts != Gst.CLOCK_TIME_NONE else int(time.monotonic_ns())

            # appsink môže dodať širší stride; ošetriť reshape a orezať na w
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
            if arr.size < w * h:
                # poškodený buffer – ignoruj
                return Gst.FlowReturn.OK
            arr = arr.reshape((h, -1))[:, :w].copy()

            # vymeniť posledný frame v 1-slot queue
            if self._q.full():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
            self._q.put_nowait(arr)

            if self._q_meta.full():
                try:
                    self._q_meta.get_nowait()
                except queue.Empty:
                    pass
            self._q_meta.put_nowait((ts, w, h))

            # voliteľne priprav GPU kópiu (iba posledný frame)
            if self._last_gpu is not None:
                try:
                    with self._gpu_lock:
                        g = cv2.cuda_GpuMat()
                        g.upload(arr)
                        self._last_gpu = g
                except Exception:
                    # ak sa upload nepodarí, ignorujeme (GPU ostane None/nutná ďalšia iterácia)
                    pass

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

    # ---- Lifecycle ----
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
        print(f"[Live] started: {self._last_pipe_str}")

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

        # vyprázdniť fronty
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                self._q_meta.get_nowait()
        except queue.Empty:
            pass

        # zrušiť GPU kópiu
        with self._gpu_lock:
            self._last_gpu = None if not _has_cuda() else cv2.cuda_GpuMat()

        print("[Live] stopped")

    # ---- Accessors ----
    def is_running(self) -> bool:
        return self._running

    def last_frame_u8(self) -> Optional[np.ndarray]:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def last_meta(self) -> Optional[Tuple[int, int, int, int]]:
        """
        Vracia (timestamp_ns, width, height, fps) pre posledný frame, ak je dostupný.
        """
        try:
            ts, w, h = self._q_meta.get_nowait()
            return ts, w, h, self.fps
        except queue.Empty:
            return None

    def last_frame_gpu(self) -> Optional["cv2.cuda_GpuMat"]:
        """
        Posledný snímok vo forme cv2.cuda_GpuMat, ak je CUDA dostupná.
        Frame je uploadnutý pri príchode sample (iba posledný).
        """
        if not _has_cuda():
            return None
        with self._gpu_lock:
            return self._last_gpu

    # ---- Debug ----
    def debug_pipeline(self) -> Optional[str]:
        return self._last_pipe_str

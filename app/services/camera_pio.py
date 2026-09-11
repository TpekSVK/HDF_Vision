"""Single-owner, two-frame CU55 PIO capture; independent of preview consumers."""
import threading
import time

from app.utils.cu55_pio import cu55_pio_profile


class CameraPioMixin:
    def _init_pio_capture(self):
        self._pio_lock = threading.RLock()
        self._pio_condition = threading.Condition()
        self._pio_request = None
        self._pio_ready = None
        self._pio_configuring = False
        self._pio_generation = 0
        self._pio_last_receive = 0.0
        self._pio_pipeline_error = None
        self._pio_unexpected = False
        self._pio_receive_count = 0
        self._pio_initialized_pipeline = None
        self._pio_recovery_required = False

    def _invalidate_pio(self):
        if not hasattr(self, "_pio_condition"):
            return
        with self._pio_condition:
            self._pio_ready = None
            self._pio_generation += 1
            if self._pio_request is not None:
                self._pio_request["cancelled"] = True
            self._pio_condition.notify_all()

    def _publish_pio_frame(self, frame, sequence):
        with self._pio_condition:
            self._pio_last_receive = time.monotonic()
            self._pio_receive_count += 1
            request = self._pio_request
            if request is None and self._pio_ready is not None:
                self._pio_unexpected = True
                self._pio_ready = None
            if request is not None and not request["cancelled"]:
                if frame.shape != (self.height, self.width):
                    request["error"] = "Nesprávne rozmery trigger snímky."
                elif request["frames"] and (
                    sequence <= request["frames"][-1][0]
                    or (not request["settling"] and sequence != request["frames"][-1][0] + 1)
                ):
                    request["error"] = f"Nesprávne poradie trigger snímok: {request['frames'][-1][0]} → {sequence}."
                elif len(request["frames"]) >= request["expected"]:
                    request["error"] = "Neočekávaná ďalšia trigger snímka."
                else:
                    request["frames"].append((sequence, frame))
            self._pio_condition.notify_all()

    def _pio_signature(self):
        return (self.device, self.width, self.height, self.fps, self.pixel_format,
                self.exposure_us, self.gain_db, getattr(self, "_brightness", None), self._pio_generation)

    def _pio_quiet(self, quiet_s=0.5, timeout_s=3.0):
        start = time.monotonic()
        with self._pio_condition:
            while True:
                now = time.monotonic()
                if self._pio_pipeline_error:
                    raise RuntimeError(self._pio_pipeline_error)
                if now - max(start, self._pio_last_receive) >= quiet_s:
                    return
                if now - start >= timeout_s:
                    raise RuntimeError("Kamera posiela snímky aj bez PIO triggeru.")
                self._pio_condition.wait(0.05)

    def _pio_burst(self, pico, profile, count=2, timeout_s=1.0, allow_missing=False, settling=False):
        request = {"frames": [], "expected": count, "error": None, "cancelled": False, "settling": settling}
        with self._pio_condition:
            if self._pio_unexpected:
                raise RuntimeError("Neočekávaná snímka mimo PIO transakcie; treba obnoviť stream.")
            if self._pio_request is not None:
                raise RuntimeError("Trigger capture už prebieha.")
            self._pio_request = request  # reserve BEFORE serial write / ACK
        try:
            result = pico.fire_pio(profile, count=count)
            deadline = time.monotonic() + timeout_s
            with self._pio_condition:
                while len(request["frames"]) < count and not request["cancelled"] and not request["error"]:
                    if self._pio_pipeline_error:
                        raise RuntimeError(self._pio_pipeline_error)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._pio_condition.wait(remaining)
                if request["cancelled"] or request["error"]:
                    raise RuntimeError(request["error"] or "Stream sa počas snímania zmenil.")
                if self._pio_pipeline_error:
                    raise RuntimeError(self._pio_pipeline_error)
                actual = len(request["frames"])
                self._logger.info("[PIO_CAPTURE] id=%s expected=%s received=%s period_us=%s settling=%s sequence=%s",
                                  result["id"], count, actual, profile.period_us, settling,
                                  [seq for seq, _ in request["frames"]])
                if actual != count and not allow_missing:
                    raise RuntimeError(f"Neúplná PIO dvojica: {actual}/{count} snímok.")
                return request["frames"][-1][1] if actual == count and not settling else None
        finally:
            with self._pio_condition:
                self._pio_request = None

    def _pio_set_mode(self, mode):
        # CU55 transitions while streaming, as in the validated lab sequence.
        # A separate reopen follows configuration; never repeat a blind SET.
        hid = self._ensure_hid()
        hid.timeout_s = 2.0
        if hid.get_stream_mode() != mode:
            hid.set_stream_mode(mode)
        if hid.get_stream_mode() != mode:
            raise RuntimeError("Kamera nepotvrdila požadovaný režim.")

    def _pio_wait_master_frames(self, count=20, timeout_s=3.0):
        deadline = time.monotonic() + timeout_s
        with self._pio_condition:
            target = self._pio_receive_count + count
            while self._pio_receive_count < target:
                if self._pio_pipeline_error:
                    raise RuntimeError(self._pio_pipeline_error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("MASTER stream nie je pripravený na prechod do TRIGGER.")
                self._pio_condition.wait(remaining)

    def prepare_pio_trigger(self, pico):
        with self._pio_lock:
            profile = cu55_pio_profile(self.width, self.height, self.fps,
                                       self.pixel_format, self.exposure_us)
            pico.require_pio()
            if self._pio_ready == self._pio_signature() and self.is_pipeline_open() and self._ensure_hid().get_stream_mode() == 1:
                pico.set_session_mode("TRIGGER")
                return profile
            pico.set_session_mode("IDLE")  # suppress external requests during transition
            self._pio_ready = None
            self._pio_configuring = True
            self._pio_pipeline_error = None
            try:
                if self._paused_external:
                    self.resume_after_external()
                self.start(caller="pio_before_transition")
                mode = self._ensure_hid().get_stream_mode()
                if mode == 1 and (self._pio_initialized_pipeline is not self._pipeline
                                  or self._pio_recovery_required):
                    self._pio_set_mode(0)
                    mode = 0
                if mode == 0:
                    self.set_manual_exposure_us(profile.exposure_us)
                    self._pio_wait_master_frames()
                self._pio_set_mode(1)
                self._pio_quiet()
                self.set_manual_exposure_us(profile.exposure_us)
                # 1080p uses the already-running MASTER stream, matching its
                # successful lab matrix. Reopening it in TRIGGER loses frames.
                # The recovery matrix required reopen for the other resolutions.
                if (self.width, self.height) != (1920, 1080):
                    self.stop(caller="pio_configure")
                    self._pio_pipeline_error = None
                    self.start(caller="pio_configure")
                if self._mode != "gst" or self._ensure_hid().get_stream_mode() != 1:
                    raise RuntimeError("PIO vyžaduje GStreamer a potvrdený TRIGGER režim.")
                self._pio_quiet()
                self._pio_unexpected = False
                self._pio_burst(pico, profile, count=1, timeout_s=0.3, allow_missing=True)
                # CU55 may deliver offsets 0,2 here after a missing arming frame.
                # Require two ordered frames but discard both; production is strict.
                self._pio_burst(pico, profile, settling=True)
                self._trigger_session_active = True
                self._trigger_session_ready = True
                self._pio_initialized_pipeline = self._pipeline
                self._pio_recovery_required = False
                self._pio_ready = self._pio_signature()
                pico.set_session_mode("TRIGGER")
                return profile
            except Exception:
                self._pio_ready = None
                self._trigger_session_ready = False
                self._pio_recovery_required = True
                raise
            finally:
                self._pio_configuring = False

    def capture_pio_frame(self, pico, timeout_s=1.0):
        with self._pio_lock:
            profile = self.prepare_pio_trigger(pico)
            self.begin_trigger_capture()
            try:
                self._trigger_last_capture_status = "normal"
                return self._pio_burst(pico, profile, timeout_s=timeout_s)
            except Exception:
                self._trigger_last_capture_status = "fail"
                self._pio_ready = None
                self._trigger_session_ready = False
                self._pio_recovery_required = True
                try:
                    pico.set_session_mode("IDLE")
                except Exception:
                    self._logger.exception("Pico IDLE po chybe snímania zlyhalo")
                # Next request must re-arm. Never return one ambiguous partial frame.
                raise
            finally:
                self.end_trigger_capture()

    def prepare_pio_master(self, pico):
        with self._pio_lock:
            if (not self._trigger_session_active and self.is_pipeline_open()
                    and self._ensure_hid().get_stream_mode() == 0):
                self.prepare_master_capture()
                pico.set_session_mode("MASTER")
                return
            pico.set_session_mode("IDLE")
            self._invalidate_pio()
            self._pio_set_mode(0)
            self.exit_trigger_session(restore_master=False)
            if self._ensure_hid().get_stream_mode() != 0:
                raise RuntimeError("Kamera nepotvrdila MASTER režim.")
            self.prepare_master_capture()
            pico.set_session_mode("MASTER")

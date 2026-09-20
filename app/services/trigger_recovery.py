"""Bounded recovery orchestration and persistent per-station audit, no Qt."""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path


def recover_trigger(runtime, request_id, original_error, budget, cancel, notify):
    attempts = 0
    started = time.monotonic()
    logger = logging.getLogger(__name__)
    def record(event, **details):
        row = dict(time=datetime.now(timezone.utc).isoformat(), event=event,
                   camera_id=runtime.camera_id, pico_id=runtime.pico_id,
                   request_id=request_id, original_error=str(original_error),
                   attempt=attempts, budget=budget, elapsed_s=time.monotonic()-started, **details)
        path = Path(runtime.data_root) / 'logs' / ('trigger_recovery_' + datetime.now(timezone.utc).strftime('%Y%m%d') + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
        logger.warning('TRIGGER_RECOVERY %s', row)
        notify(row)
    try:
        record('started')
        for _ in range(min(2, budget)):
            if cancel.is_set():
                raise RuntimeError('Obnova zrušená operátorom.')
            attempts += 1
            record('attempt_started')
            try:
                runtime.cam.pio.recover_trigger_mode(runtime.pico, cancel=cancel,
                    diagnostic=lambda **details: record('mode_read_retry', **details))
                if cancel.is_set():
                    raise RuntimeError('Obnova zrušená operátorom.')
                record('succeeded')
                return dict(ok=True, attempts=attempts, error='')
            except Exception as exc:
                runtime.pico.quiesce()
                record('attempt_failed', error=str(exc))
                if cancel.is_set():
                    raise RuntimeError('Obnova zrušená operátorom.') from exc
        raise RuntimeError('Automatická obnova zlyhala; vyžaduje sa zásah operátora.')
    except Exception as exc:
        try:
            runtime.pico.quiesce()
        except Exception as cleanup:
            logger.exception('Pico quiesce after recovery failed')
            exc = RuntimeError(f'{exc}; zastavenie Pico: {cleanup}')
        try:
            record('cancelled' if cancel.is_set() else 'failed', error=str(exc))
        except Exception:
            logger.exception('Cannot persist recovery failure')
        return dict(ok=False, attempts=attempts, error=str(exc))

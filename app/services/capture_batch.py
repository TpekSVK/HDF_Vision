"""Stage capture PNGs on a single worker; publish only the confirmed selection."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import imageio.v3 as iio
import numpy as np

from app.services.learning_context import bind_samples, MANIFEST_NAME
from app.services.presence_absence_v2_service import _as_gray_u8


class CaptureBatch:
    def __init__(self, assets, mode, signature, shared_directory=None):
        self.assets = Path(assets)
        self.mode = mode
        self.signature = signature
        self.shared_directory = Path(shared_directory) if shared_directory is not None else None
        self.assets.mkdir(parents=True, exist_ok=True)
        self._local = TemporaryDirectory(prefix='.capture-', dir=self.assets)
        self._shared = None
        try:
            if self.shared_directory is not None:
                self.shared_directory.mkdir(parents=True, exist_ok=True)
                self._shared = TemporaryDirectory(prefix='.capture-', dir=self.shared_directory)
        except Exception:
            self._local.cleanup()
            raise
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='capture-save')
        self._writes = {}
        self._closed = False

    def add(self, sample, frame=None):
        """The caller transfers immutable array snapshots to the worker."""
        if self._closed:
            raise RuntimeError('Zber je už ukončený.')
        ident = uuid4().hex
        self._writes[ident] = self._executor.submit(self._write, ident, sample, frame)
        return ident

    def _write(self, ident, sample, frame):
        iio.imwrite(Path(self._local.name) / f'{ident}.png', _as_gray_u8(sample))
        if self._shared is not None:
            if frame is None:
                raise ValueError('Chýba celá OK snímka.')
            iio.imwrite(Path(self._shared.name) / f'{ident}.png', np.asarray(frame, dtype=np.uint8))

    def commit(self, selected):
        return self._executor.submit(self._commit, tuple(selected))

    def _commit(self, selected):
        # All encoding tasks precede this job. Deleted samples never get published.
        for ident in selected:
            self._writes[ident].result()
        if not selected:
            return 0
        manifest = self.assets / MANIFEST_NAME
        previous = manifest.read_bytes() if manifest.exists() else None
        moved = []
        try:
            bind_samples(self.assets, self.signature)
            destination = self.assets / self.mode
            destination.mkdir(parents=True, exist_ok=True)
            for ident in selected:
                pairs = [(Path(self._local.name) / f'{ident}.png',
                          destination / f'{"shared" if self._shared else "sample"}_{ident}.png')]
                if self._shared is not None:
                    pairs.append((Path(self._shared.name) / f'{ident}.png',
                                  self.shared_directory / f'{ident}.png'))
                for source, target in pairs:
                    source.replace(target)
                    moved.append((source, target))
        except Exception:
            # Restore the staging files so a failed commit can be retried.
            for source, target in reversed(moved):
                target.replace(source)
            if previous is None:
                manifest.unlink(missing_ok=True)
            else:
                manifest.write_bytes(previous)
            raise
        return len(selected)

    def discard(self):
        """Cancel queued writes; clean up after any in-flight write, without waiting."""
        if self._closed:
            return
        self._closed = True
        for future in self._writes.values():
            future.cancel()
        self.cleanup_future = self._executor.submit(self._cleanup)
        self._executor.shutdown(wait=False)

    def _cleanup(self):
        self._local.cleanup()
        if self._shared is not None:
            self._shared.cleanup()

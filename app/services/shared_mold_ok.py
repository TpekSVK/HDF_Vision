"""Shared full OK frames; ROI crops, NOK datasets and models remain tool-local."""
from pathlib import Path
import shutil
import imageio.v3 as iio
import numpy as np
from uuid import uuid4
from tempfile import TemporaryDirectory
from app.services.learning_context import bind_samples


class SharedMoldOK:
    @staticmethod
    def clear_view(base, recipe, view_id):
        """Reset every capture context in this view, including older settings."""
        directory = Path(base) / 'recipes' / recipe / 'shared_mold_ok' / view_id
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            if directory.exists():
                raise

    def __init__(self, base, recipe, view_id, capture_signature):
        self.directory = (Path(base) / 'recipes' / recipe / 'shared_mold_ok'
                          / view_id / capture_signature)

    def add(self, frame):
        # Store the original, oriented frame. The existing preparation path
        # performs locator alignment and target-specific preprocessing on reuse.
        self.directory.mkdir(parents=True, exist_ok=True)
        ident = uuid4().hex
        path = self.directory / f'{ident}.png'
        temporary = path.with_suffix('.tmp.png')
        try:
            # Preserve color channels too: locator preparation uses the same
            # original pixels as during the initial collection.
            iio.imwrite(temporary, np.asarray(frame, dtype=np.uint8))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return ident

    def frames(self):
        for path in sorted(self.directory.glob('*.png')):
            yield path.stem, np.asarray(iio.imread(path))

    def import_into(self, assets, signature, prepare):
        assets = Path(assets)
        frames = list(self.directory.glob('*.png'))
        if not frames:
            return 0
        # Refuse mixing local NOK/OK samples from a different target context.
        bind_samples(assets, signature)
        destination = assets / 'ok'
        destination.mkdir(parents=True, exist_ok=True)
        count = 0
        with TemporaryDirectory(dir=assets) as staging:
            pending = []
            for source in sorted(frames):
                path = destination / f'shared_{source.stem}.png'
                if path.exists():
                    continue
                frame = np.asarray(iio.imread(source))
                sample = prepare(frame)
                if sample is None:
                    raise ValueError('Spoločná snímka nemá platnú ROI pre tento nástroj.')
                temporary = Path(staging) / path.name
                iio.imwrite(temporary, np.asarray(sample, dtype=np.uint8))
                pending.append((temporary, path))
            for temporary, path in pending:
                temporary.replace(path)
                count += 1
        return count

"""Capture persistence must not block Qt or publish unconfirmed/deleted frames."""
import time
from threading import Event

import imageio.v3 as iio
import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.services.capture_batch import CaptureBatch
from app.services.learning_context import bind_samples, samples_match
from app.ui.golden_wizard.presence_v2_sample_capture_dialog import PresenceV2SampleCaptureDialog


def pump_until(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            pytest.fail('Qt save did not finish')
        time.sleep(.005)


def test_selected_pairs_and_pixels_are_preserved(tmp_path):
    batch = CaptureBatch(tmp_path / 'local', 'ok', 'sig', tmp_path / 'shared')
    frame = np.random.default_rng(42).integers(0, 256, (20, 30, 3), dtype=np.uint8)
    sample = frame[:8, :9, 0].copy()
    first = batch.add(sample, frame)
    batch.add(sample + 1, frame)  # Deleted from the dialog's selection.
    assert batch.commit([first]).result(timeout=5) == 1
    assert samples_match(batch.assets, 'sig')
    paths = list((batch.assets / 'ok').glob('*.png'))
    assert len(paths) == 1 and paths[0].name == f'shared_{first}.png'
    np.testing.assert_array_equal(iio.imread(paths[0]), sample)
    np.testing.assert_array_equal(iio.imread(tmp_path / 'shared' / f'{first}.png'), frame)
    batch.discard(); batch.cleanup_future.result(timeout=5)
    assert not list(tmp_path.rglob('.capture-*'))


def test_cancel_does_not_wait_for_writer_or_publish(tmp_path, monkeypatch):
    batch = CaptureBatch(tmp_path, 'nok', 'sig')
    entered, release = Event(), Event()
    write = batch._write
    def slow(*args):
        entered.set()
        assert release.wait(5)
        write(*args)
    monkeypatch.setattr(batch, '_write', slow)
    batch.add(np.zeros((8, 8), np.uint8))
    assert entered.wait(5)
    try:
        batch.discard()
        assert not batch.cleanup_future.done()
        assert not (tmp_path / 'nok').exists()
    finally:
        release.set()
    batch.cleanup_future.result(timeout=5)
    assert not list(tmp_path.iterdir())


def test_encoding_error_never_publishes_partial_batch(tmp_path, monkeypatch):
    batch = CaptureBatch(tmp_path, 'ok', 'sig')
    first = batch.add(np.zeros((8, 8), np.uint8))
    batch._writes[first].result(timeout=5)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(batch, '_write', fail)
    second = batch.add(np.ones((8, 8), np.uint8))
    with pytest.raises(OSError, match='disk full'):
        batch.commit([first, second]).result(timeout=5)
    assert not (tmp_path / 'ok').exists()
    batch.discard(); batch.cleanup_future.result(timeout=5)


def test_commit_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    from pathlib import Path
    batch = CaptureBatch(tmp_path / 'local', 'ok', 'sig', tmp_path / 'shared')
    bind_samples(batch.assets, 'sig')
    original_manifest = (batch.assets / 'sample_context.json').read_bytes()
    ident = batch.add(np.zeros((8, 8), np.uint8), np.zeros((10, 10, 3), np.uint8))
    replace = Path.replace
    def fail_shared(source, target):
        if target == tmp_path / 'shared' / f'{ident}.png':
            raise OSError('rename failed')
        return replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_shared)
        with pytest.raises(OSError, match='rename failed'):
            batch.commit([ident]).result(timeout=5)
    assert not list((batch.assets / 'ok').glob('*.png'))
    assert (batch.assets / 'sample_context.json').read_bytes() == original_manifest
    assert batch.commit([ident]).result(timeout=5) == 1
    batch.discard(); batch.cleanup_future.result(timeout=5)


def test_dialog_stays_responsive_while_finishing(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    batch = CaptureBatch(tmp_path, 'ok', 'sig')
    release, entered = Event(), Event()
    original = batch._write
    def slow(*args):
        entered.set()
        assert release.wait(5)
        original(*args)
    monkeypatch.setattr(batch, '_write', slow)
    d = PresenceV2SampleCaptureDialog(title='test', capture_fn=lambda:np.zeros((8, 8), np.uint8),
                                     crop_fn=lambda f:f, batch=batch)
    d.show(); d._capture_once()
    assert entered.wait(5)
    d._auto_timer.start(10000)
    ticks = []
    try:
        d.accept()
        assert not d._auto_timer.isActive()
        assert d.isVisible()
        QTimer.singleShot(0, lambda:ticks.append(True))
        pump_until(app, lambda:bool(ticks))
        assert not d._commit_future.done()
        d.reject()  # Cannot abandon a batch in the middle of publishing.
        assert d.isVisible()
    finally:
        release.set()
    pump_until(app, lambda:not d.isVisible())
    assert d.result() == QDialog.Accepted and d.saved_count == 1
    batch.cleanup_future.result(timeout=5)
    d.deleteLater()


@pytest.mark.parametrize('action', ['accept', 'reject', 'close'])
def test_every_exit_stops_capture_timer(action):
    app = QApplication.instance() or QApplication([])
    d = PresenceV2SampleCaptureDialog(title='test', capture_fn=lambda:None, crop_fn=lambda f:f)
    d.show(); d._auto_timer.start(10000)
    getattr(d, action)()
    assert not d._auto_timer.isActive()
    d.deleteLater()


def test_changed_context_keeps_batch_unpublished(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    batch = CaptureBatch(tmp_path, 'ok', 'sig')
    def changed():
        raise ValueError('changed context')
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args:errors.append(args[-1]))
    d = PresenceV2SampleCaptureDialog(title='test', capture_fn=lambda:np.zeros((8, 8), np.uint8),
                                     crop_fn=lambda f:f, batch=batch, validate_commit=changed)
    d._capture_once(); d.accept()
    assert errors == ['changed context'] and d._commit_future is None
    assert not (tmp_path / 'ok').exists()
    d.reject(); batch.cleanup_future.result(timeout=5)
    d.deleteLater()

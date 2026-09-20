"""Additive SQLite catalogue and retained lossless frames / immutable model artifacts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import uuid
import cv2
import numpy as np
from PIL import Image
from app.services.empty_mold_v2.model import VariationModel

log = logging.getLogger(__name__)
STATES = {'training_ok', 'training_nok', 'validation_ok', 'validation_nok',
          'candidate_ok', 'candidate_nok', 'rejected'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS empty_mold_v2_samples (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL,
 state TEXT NOT NULL, frame_path TEXT NOT NULL, frame_hash TEXT NOT NULL,
 metadata_json TEXT NOT NULL, annotations_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emv2_samples_owner ON empty_mold_v2_samples(owner, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_emv2_approved_frame ON empty_mold_v2_samples(owner, frame_hash)
 WHERE state IN ('training_ok','training_nok','validation_ok','validation_nok');
CREATE TABLE IF NOT EXISTS empty_mold_v2_models (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL,
 artifact_path TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emv2_models_owner ON empty_mold_v2_models(owner);
CREATE TABLE IF NOT EXISTS empty_mold_v2_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
 created_at TEXT NOT NULL, action TEXT NOT NULL, details_json TEXT NOT NULL
);
'''


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class DatasetStore:
    def __init__(self, db_path, assets, owner):
        self.db_path, self.assets, self.owner = Path(db_path), Path(assets), str(owner)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.assets.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, action, details):
        with self.connection() as db:
            db.execute('INSERT INTO empty_mold_v2_audit(owner,created_at,action,details_json) VALUES(?,?,?,?)',
                       (self.owner, timestamp(), action, encoded(details)))
        log.info('Empty Mold V2 %s owner=%s %s', action, self.owner, details)

    def add(self, frame, state, metadata, annotations=()):
        if state not in STATES or state == 'rejected':
            raise ValueError('Neplatný stav vzorky.')
        if state in ('training_nok', 'validation_nok') and not annotations:
            raise ValueError('Potvrdená NOK vzorka potrebuje polygón.')
        frame = np.ascontiguousarray(frame)
        if frame.ndim != 2 or frame.dtype != np.uint8:
            raise ValueError('Ukladajte celý zarovnaný grayscale uint8 frame.')
        digest = hashlib.sha256(str(frame.shape).encode() + frame.tobytes()).hexdigest()
        with self.connection() as db:
            previous = db.execute('SELECT id,frame_path,state FROM empty_mold_v2_samples WHERE owner=? AND frame_hash=?',
                                  (self.owner, digest)).fetchone()
            if previous and not state.startswith('candidate_'):
                raise ValueError('Rovnaká snímka už je v datasete; nemožno ju duplikovať ani rozdeliť medzi tréning a validáciu.')
        ident = uuid.uuid4().hex
        directory = self.assets / 'frames'
        directory.mkdir(exist_ok=True)
        path = directory / f'{ident}.webp'
        if previous and Path(previous['frame_path']).is_file():
            path = Path(previous['frame_path'])
            created = False
        else:
            temporary = directory / f'.{ident}.tmp'
            Image.fromarray(frame).save(temporary, format='WEBP', lossless=True, method=4)
            temporary.replace(path)
            created = True
        try:
            with self.connection() as db:
                db.execute('INSERT INTO empty_mold_v2_samples VALUES(?,?,?,?,?,?,?,?)',
                           (ident, self.owner, timestamp(), state, str(path), digest,
                            encoded(metadata), encoded(list(annotations))))
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise
        self.audit('sample_added', dict(id=ident, state=state, source=metadata.get('feedback_source')))
        return ident

    def samples(self, states=None):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM empty_mold_v2_samples WHERE owner=? ORDER BY created_at,id',
                              (self.owner,)).fetchall()
        return [{**dict(r), 'metadata': json.loads(r['metadata_json']),
                 'annotations': json.loads(r['annotations_json'])} for r in rows
                if states is None or r['state'] in states]

    @staticmethod
    def frame(sample):
        path = Path(sample['frame_path'])
        if not path.is_file():
            raise ValueError('Plná snímka už nie je dostupná.')
        with Image.open(path) as image:
            frame = np.asarray(image.convert('L')).copy()
        return frame

    def review(self, ident, state, annotations, metadata):
        if state not in STATES or state.startswith('candidate'):
            raise ValueError('Neplatné rozhodnutie technika.')
        if state.endswith('_nok') and not annotations:
            raise ValueError('NOK vzorka potrebuje polygonovú anotáciu.')
        with self.connection() as db:
            row = db.execute('SELECT * FROM empty_mold_v2_samples WHERE id=? AND owner=?', (ident, self.owner)).fetchone()
            if row is None:
                raise ValueError('Vzorka neexistuje.')
            if state != 'rejected':
                duplicate = db.execute(
                    "SELECT id FROM empty_mold_v2_samples WHERE owner=? AND frame_hash=? AND id!=? "
                    "AND state IN ('training_ok','training_nok','validation_ok','validation_nok')",
                    (self.owner, row['frame_hash'], ident)).fetchone()
                if duplicate:
                    raise ValueError('Rovnaká snímka už je potvrdená v datasete. Kandidáta nemožno pridať druhýkrát.')
            # Preserve original production provenance and feedback label.
            combined = {**json.loads(row['metadata_json']), **metadata, 'reviewed_at': timestamp()}
            db.execute('UPDATE empty_mold_v2_samples SET state=?,annotations_json=?,metadata_json=? WHERE id=?',
                       (state, encoded(annotations), encoded(combined), ident))
        self.audit('sample_review', dict(id=ident, previous=row['state'], state=state))

    def save_model(self, model, metadata):
        ident = uuid.uuid4().hex
        directory = self.assets / 'models' / ident
        directory.mkdir(parents=True)
        path = directory / 'variation.npz'
        np.savez_compressed(path, center=model.center, variability=model.variability)
        combined = {**model.metadata, **metadata, 'id': ident, 'created_at': timestamp()}
        (directory / 'metadata.json').write_text(encoded(combined), encoding='utf-8')
        with self.connection() as db:
            db.execute('INSERT INTO empty_mold_v2_models VALUES(?,?,?,?,?)',
                       (ident, self.owner, combined['created_at'], str(path), encoded(combined)))
        self.audit('model_saved', dict(id=ident, training_count=len(combined.get('training_ids', [])),
                                     training_nok_count=combined.get('training_nok_count', 0)))
        return ident, path

    def models(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM empty_mold_v2_models WHERE owner=? ORDER BY created_at', (self.owner,))]


def load_model(path):
    path = Path(path)
    metadata = json.loads((path.parent / 'metadata.json').read_text(encoding='utf-8'))
    with np.load(path, allow_pickle=False) as data:
        center, variability = data['center'].astype('float32'), data['variability'].astype('float32')
    if (center.ndim != 2 or center.shape != variability.shape or not np.isfinite(center).all()
            or not np.isfinite(variability).all() or (variability <= 0).any()):
        raise ValueError('Neplatný modelový artefakt.')
    return VariationModel(center, variability, metadata)

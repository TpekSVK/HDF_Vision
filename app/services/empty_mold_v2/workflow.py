"""Technician workflow. Only explicit activation changes the recipe model pointer."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
import numpy as np
from app.services.empty_mold_v2 import regions
from app.services.empty_mold_v2.model import build
from app.services.empty_mold_v2.evaluator import settings
from app.services.empty_mold_v2.storage import DatasetStore, encoded, load_model
from app.services.empty_mold_v2.validation import validate, recommend

TYPE = 'mold.protection_v2'


def bind_store(tool, db_path, recipes_base, recipe, view_id):
    p = tool.params.values
    owner = p.setdefault('v2_owner', uuid.uuid4().hex)
    # Generated ID, never a user-provided filename.
    if len(owner) != 32 or any(c not in '0123456789abcdef' for c in owner):
        raise ValueError('Neplatná identita V2 toolu.')
    assets = Path(recipes_base) / '.empty_mold_v2' / owner
    p['v2_assets'] = str(assets)
    return DatasetStore(db_path, assets, owner)


def context_id(tool, shape, capture_signature):
    return hashlib.sha256(encoded({'regions': regions.signature(tool, shape),
                                  'capture': capture_signature}).encode()).hexdigest()


class Workflow:
    def __init__(self, tool, store, golden, capture_signature, authorize):
        self.tool, self.store, self.golden = tool, store, golden
        self.capture_signature = capture_signature
        self.authorize = authorize
        self.zones = regions.zones_for(tool, golden.shape[:2])
        self.context = context_id(tool, golden.shape[:2], capture_signature)
        self.candidate = None
        self.validation = None
        self.recommendation = None
        self.previous = None

    def require_technician(self):
        if not self.authorize():
            raise PermissionError('Vyžaduje odomknutie editácie receptu.')

    def metadata(self, annotations=()):
        masks = regions.annotation_masks(annotations, self.zones)
        directory = self.store.assets / 'contexts' / self.context
        directory.mkdir(parents=True, exist_ok=True)
        masks_path = directory / 'masks.npz'
        if not masks_path.exists():
            main = regions.shape_mask(self.golden.shape[:2], self.tool.roi)
            valid = np.logical_or.reduce(list(self.zones.values()))
            np.savez_compressed(masks_path, main_roi=main.astype('uint8'),
                                ignore=(main & ~valid).astype('uint8'),
                                **{f'zone_{i}': mask.astype('uint8') for i, mask in enumerate(self.zones.values())})
            (directory / 'zones.json').write_text(encoded(list(self.zones)), encoding='utf-8')
        return dict(context=self.context, capture_signature=self.capture_signature,
                    masks_path=str(masks_path), zone_names=list(self.zones),
                    geometry={'main_roi': self.tool.roi.to_dict(),
                              'cavities': deepcopy(self.tool.params.values.get('cavities', []))},
                    annotation_zones=[[name for name, zone in self.zones.items() if (mask & zone).any()] for mask in masks])

    def add_sample(self, frame, state, annotations=()):
        self.require_technician()
        if state not in ('training_ok', 'training_nok', 'validation_ok', 'validation_nok'):
            raise ValueError('Vyberte tréning alebo validáciu.')
        if frame.shape != self.golden.shape[:2]:
            raise ValueError('Nesprávne rozmery zarovnanej snímky.')
        return self.store.add(frame, state, self.metadata(annotations), annotations)

    def review(self, sample_id, state, annotations=()):
        self.require_technician()
        sample = next(s for s in self.store.samples() if s['id'] == sample_id)
        if state != 'rejected' and sample['metadata'].get('context') != self.context:
            raise ValueError('Vzorka patrí inej geometrii / kamere / zarovnaniu; nemožno ju prijať do tohto datasetu.')
        if state != 'rejected' and self.store.frame(sample).shape != self.golden.shape[:2]:
            raise ValueError('Nesprávne rozmery snímky.')
        self.store.review(sample_id, state, annotations, {**self.metadata(annotations),
                         'approval_status': 'rejected' if state == 'rejected' else 'approved'})
        self.validation = None

    def compatible_samples(self, states):
        return [s for s in self.store.samples(states) if s['metadata'].get('context') == self.context]

    def build(self):
        self.require_technician()
        samples = self.compatible_samples({'training_ok'})
        if len(samples) < 2:
            raise ValueError('Potrebné sú aspoň 2 potvrdené tréningové OK vzorky. Odporúčame 30, ideálne 50+.')
        p = settings(self.tool)
        zones = regions.zones_for(self.tool, self.golden.shape[:2], ignore_border=p['ignore_border'])
        # uint8 disk-backed stack: bounded RAM even for 50 full-resolution frames.
        with tempfile.TemporaryDirectory(dir=self.store.assets) as directory:
            stack = np.memmap(Path(directory) / 'frames.u8', dtype='uint8', mode='w+',
                              shape=(len(samples), *self.golden.shape[:2]))
            try:
                for i, sample in enumerate(samples):
                    frame = self.store.frame(sample)
                    if frame.shape != self.golden.shape[:2]:
                        raise ValueError('Nekompatibilná vzorka.')
                    stack[i] = frame
                model = build(stack, zones, normalize=p['normalize'], variability_floor=p['variability_floor'],
                              variability_cap=p['variability_cap'])
            finally:
                del stack
        ident, path = self.store.save_model(model, dict(context=self.context, capture_signature=self.capture_signature,
                                                       geometry_signature=regions.signature(self.tool, self.golden.shape[:2]),
                                                       training_ids=[s['id'] for s in samples], parameters=p,
                                                       training_nok_count=len(self.compatible_samples({'training_nok'})),
                                                       state='candidate', geometry=self.metadata()['geometry'],
                                                       masks_path=self.metadata()['masks_path'], zone_names=list(self.zones)))
        self.candidate = load_model(path)
        self.validation = None
        return self.candidate

    def candidate_model(self):
        if self.candidate is None:
            raise ValueError('Najskôr vytvorte alebo vyberte kandidátsky model.')
        p = settings(self.tool)
        old = self.candidate.metadata['parameters']
        if self.candidate.metadata['context'] != self.context:
            raise ValueError('Model patrí inému nastaveniu.')
        for key in ('normalize', 'variability_floor', 'variability_cap', 'ignore_border'):
            if p[key] != old[key]:
                raise ValueError('Zmena prípravy obrazu vyžaduje nový prepočet modelu.')
        return self.candidate

    @staticmethod
    def dataset_fingerprint(samples):
        return hashlib.sha256(encoded([{key: sample[key] for key in
            ('id', 'state', 'frame_hash', 'annotations', 'metadata')} for sample in samples]).encode()).hexdigest()

    def test(self):
        model = self.candidate_model()
        samples = self.compatible_samples({'validation_ok', 'validation_nok'})
        if not samples:
            raise ValueError('Pridajte samostatné validačné vzorky. Test nikdy neučí model.')
        p = settings(self.tool)
        zones = regions.zones_for(self.tool, self.golden.shape[:2], ignore_border=p['ignore_border'])
        report = validate(samples, self.store.frame, model, zones, p)
        self.validation = dict(parameters=deepcopy(p), report=report, sample_ids=[s['id'] for s in samples],
                               dataset_fingerprint=self.dataset_fingerprint(samples))
        self.store.audit('validation', {k: v for k, v in report.items() if k != 'samples'})
        return report

    def suggest(self):
        self.require_technician()
        model = self.candidate_model()
        samples = self.compatible_samples({'training_ok', 'training_nok'})
        p = settings(self.tool)
        zones = regions.zones_for(self.tool, self.golden.shape[:2], ignore_border=p['ignore_border'])
        self.recommendation = recommend(samples, self.store.frame, model, zones, p)
        return self.recommendation

    def apply_recommended(self):
        self.require_technician()
        if not self.recommendation:
            raise ValueError('Najskôr prepočítajte odporúčania.')
        self.previous = deepcopy(settings(self.tool))
        self._apply(self.recommendation['recommended'])

    def _apply(self, p):
        from app.services.empty_mold_v2.evaluator import DEFAULTS
        self.tool.params.values.update({key: deepcopy(p[key]) for key in DEFAULTS})
        for key in DEFAULTS:
            self.tool.thresholds.values.pop(key, None)
        self.validation = None

    def undo(self):
        self.require_technician()
        if self.previous is not None:
            self._apply(self.previous)
            self.previous = None

    def activate(self):
        self.require_technician()
        model = self.candidate_model()
        p = settings(self.tool)
        if not self.validation or self.validation['parameters'] != p:
            raise ValueError('Pred aktiváciou otestujte aktuálne nastavenia na validačnom datasete.')
        # Prevent activation after review/removal/addition since last validation.
        samples = self.compatible_samples({'validation_ok', 'validation_nok'})
        if self.dataset_fingerprint(samples) != self.validation['dataset_fingerprint']:
            raise ValueError('Validačný dataset sa zmenil. Zopakujte test.')
        report = self.validation['report']
        if not report['ok_correct'] or not report['nok_correct'] or report['false_ok']:
            raise ValueError('Aktivácia potrebuje validačné OK aj NOK a žiadne False OK.')
        # New immutable version freezes parameters; old artifacts remain available.
        metadata = {**model.metadata, 'parameters': deepcopy(p), 'validation': deepcopy(self.validation),
                    'parent_id': model.metadata['id'], 'state': 'validated'}
        for key in ('v2_active_model', 'v2_previous_model', 'v2_assets', 'v2_owner'):
            metadata['parameters'].pop(key, None)
        ident, path = self.store.save_model(model, metadata)
        previous = self.tool.params.values.get('v2_active_model')
        self.tool.params.values['v2_previous_model'] = previous
        self.tool.params.values['v2_active_model'] = str(path)
        self.store.audit('explicit_activation', dict(model_id=ident, previous=previous))
        return ident

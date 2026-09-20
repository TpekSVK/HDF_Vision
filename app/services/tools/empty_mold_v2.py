"""Independent, fail-closed Empty Mold V2 runtime using an explicitly activated version."""
from functools import lru_cache
from pathlib import Path
import time
from app.services.tool_contracts import BaseTool, ToolRunResult
from app.services.empty_mold_v2.alignment import aligned_frame
from app.services.empty_mold_v2.evaluator import evaluate, DEFAULTS
from app.services.empty_mold_v2.regions import signature, zones_for
from app.services.empty_mold_v2.storage import load_model


@lru_cache(maxsize=4)
def _model(path, modified):
    return load_model(path)


class EmptyMoldV2Tool(BaseTool):
    def run(self, golden, frame, params, thresholds, context):
        started = time.perf_counter()
        try:
            tool = self._prepared_context['tool']
            if not self._prepared_context.get('learning_alignment_valid', True):
                raise ValueError('Locator zlyhal.')
            path = str(params.values.get('v2_active_model') or '')
            if not path:
                raise ValueError('Nie je aktivovaný model V2.')
            model = _model(path, Path(path).stat().st_mtime_ns)
            if model.metadata.get('state') != 'validated':
                raise ValueError('Model nie je validovaný.')
            if model.metadata.get('geometry_signature') != signature(tool, golden.shape[:2]):
                raise ValueError('Geometria sa zmenila. Aktivujte nový model.')
            expected = self._prepared_context.get('learning_signature')
            if not expected or model.metadata.get('capture_signature') != expected:
                raise ValueError('Zmenil sa golden, kamera alebo Locator. Aktivujte kompatibilný model.')
            p = model.metadata['parameters']
            image = aligned_frame(self._prepared_context['runner_context'])
            zones = zones_for(tool, image.shape, ignore_border=p['ignore_border'])
            result = evaluate(image, model, zones, p)
            metrics = {key: result[key] for key in ('blobs', 'blob_count', 'largest_blob_area', 'zones')}
            transform = self._prepared_context['runner_context'].T_total
            metrics.update(alignment_transform=transform.tolist() if transform is not None else None,
                           model_version=model.metadata['id'], inspection_fault=False,
                           mold_empty=result['status'] == 'ok', decision_reason='variation_model',
                           v2_owner=params.values.get('v2_owner'), v2_assets=params.values.get('v2_assets'),
                           v2_context=model.metadata['context'], v2_model_path=path)
            from app.services.empty_mold_v2.overlays import items as overlay_items
            artifacts = {'type': 'mold.protection_v2', 'diagnostics': metrics,
                         'display_items': [item for item in overlay_items(tool, metrics, affine=transform)
                                           if item.z_index >= 30]}
            if self._prepared_context.get('capture_filtered_roi'):
                self.filtered_roi = {'image': (result['binary'] * 255), 'rect': (0, 0, image.shape[1], image.shape[0]),
                                     'mask': result['binary'] > 0, 'label': 'V2 – spoločná anomaly maska'}
            status = result['status']
        except (ValueError, KeyError, OSError, TypeError) as exc:
            status = 'nok'
            metrics = dict(inspection_fault=True, mold_empty=False, decision_reason=str(exc), blob_count=0)
            artifacts = {'type': 'mold.protection_v2', 'diagnostics': metrics}
        elapsed = (time.perf_counter() - started) * 1000
        self.last_diagnostics = metrics
        return ToolRunResult(status, metrics, elapsed, artifacts)


def registry_metadata():
    return dict(name='Kontrola prázdnej formy V2', category='Presence', supports_roi=True,
                supports_ignore_mask=True,
                description='Experimentálna kontrola kavit aj priestoru medzi nimi. Model z potvrdených OK snímok; explicitná validácia a aktivácia.',
                schema={'params': {
                    'sensitivity': dict(type='int', default=60, min=1, max=100, label='Citlivosť'),
                    'min_blob_area': dict(type='int', default=20, min=1, max=100000000, label='Minimálna plocha defektu (px)'),
                    'polarity': dict(type='enum', default='both', label='Polarita',
                                     choices=[('bright', 'Svetlejší'), ('dark', 'Tmavší'), ('both', 'Oboje')]),
                    'max_blob_count': dict(type='int', default=0, min=0, max=10000, label='Povolený počet blobov'),
                }, 'thresholds': {}},
                metrics_spec=[dict(key='blob_count', priority=10, description='Počet blobov'),
                              dict(key='largest_blob_area', priority=9, unit='px', description='Najväčší blob')])

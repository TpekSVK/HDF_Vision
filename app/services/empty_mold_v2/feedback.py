"""Operator feedback ONLY creates retained candidates; never touches recipes/models."""
from pathlib import Path
import cv2
import numpy as np
from app.utils import imaging
from app.services.empty_mold_v2.storage import DatasetStore, load_model


def add_feedback(db_path, record, metadata, report, label, *, frame=None):
    if report.get('type') != 'mold.protection_v2' or label not in ('false_nok', 'false_ok'):
        raise ValueError('Vyberte výsledok Empty Mold V2 a platný feedback.')
    if (label == 'false_nok' and report.get('status') != 'nok') or (label == 'false_ok' and report.get('status') != 'ok'):
        raise ValueError('Feedback nezodpovedá pôvodnému výsledku toolu.')
    metrics = report.get('metrics', {})
    if metrics.get('inspection_fault'):
        raise ValueError('Chyba modelu / zarovnania nie je vhodná tréningová vzorka.')
    owner, assets = metrics.get('v2_owner'), metrics.get('v2_assets')
    if not owner or not assets or not metrics.get('v2_context'):
        raise ValueError('Výsledok nemá údaje o verzii modelu.')
    root = (Path(db_path).parent / 'recipes').resolve()
    if not Path(assets).resolve().is_relative_to(root):
        raise ValueError('Dataset neleží v úložisku receptov.')
    if frame is None:
        path = record.get('full_path')
        if not path or not Path(path).is_file():
            raise ValueError('Plná produkčná snímka už nie je dostupná; thumbnail nemožno použiť na učenie.')
        frame = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise ValueError('Plnú snímku nemožno načítať.')
    alignment = metadata.get('empty_mold_v2_alignment')
    if not isinstance(alignment, dict) or not alignment.get('valid'):
        raise ValueError('Chýba spoľahlivý záznam zarovnania pôvodného výsledku.')
    matrix = alignment.get('T_total')
    if matrix is not None:
        frame = imaging.warp_by_affine_u8(frame, imaging.invert_affine(np.asarray(matrix, 'float32')))
    store = DatasetStore(db_path, assets, owner)
    state = 'candidate_ok' if label == 'false_nok' else 'candidate_nok'
    model = load_model(metrics['v2_model_path'])
    if frame.shape != model.center.shape:
        raise ValueError('Produkčná snímka nemá pôvodné rozmery.')
    return store.add(frame, state, dict(
        timestamp=record.get('ts_ms', metadata.get('ts_ms')), recipe_id=record.get('recipe_id', metadata.get('recipe_id')),
        recipe=metadata.get('recipe'), recipe_version=metadata.get('recipe_version'),
        tool_id=report.get('id'), model_version=metrics.get('model_version'),
        original_system_result=report.get('status'), zone_results=metrics.get('zones'),
        feedback_label=label, feedback_source='production_result', approval_status='pending',
        source_result_id=record.get('id'), source_frame=record.get('full_path'),
        context=metrics['v2_context'], geometry=model.metadata.get('geometry'),
        masks_path=model.metadata.get('masks_path'), zone_names=model.metadata.get('zone_names'),
        capture_signature=model.metadata.get('capture_signature'), alignment=alignment,
    ))

"""Portable lossless dataset archive for future classical / AI tools."""
import json
from pathlib import Path
import zipfile


def export_dataset(store, destination):
    samples = store.samples()
    manifest = dict(format='hdf-empty-mold-v2-dataset', version=1, owner=store.owner, samples=[])
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        added = set()
        for sample in samples:
            entry = {k: v for k, v in sample.items() if k not in ('metadata_json', 'annotations_json')}
            frame = Path(sample['frame_path'])
            name = f'frames/{sample["id"]}.webp'
            archive.write(frame, name)
            entry['frame_path'] = name
            masks = sample['metadata'].get('masks_path')
            if masks:
                masks = Path(masks)
                context = sample['metadata']['context']
                for path in (masks, masks.parent / 'zones.json'):
                    target = f'contexts/{context}/{path.name}'
                    if target not in added:
                        archive.write(path, target)
                        added.add(target)
                entry['metadata'] = {**entry['metadata'], 'masks_path': f'contexts/{context}/masks.npz'}
            manifest['samples'].append(entry)
        for model in store.models():
            archive.writestr(f'models/{model["id"]}.json', model['metadata_json'])
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    store.audit('dataset_export', dict(samples=len(samples), destination=str(destination)))

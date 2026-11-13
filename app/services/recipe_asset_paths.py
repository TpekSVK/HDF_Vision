"""Relocate recipe-local learning pointers without changing model contents."""
from pathlib import Path
import json


def relocated_document(raw, old_root, new_root):
    old_root, new_root = Path(old_root).resolve(), Path(new_root).resolve()
    def walk(value):
        if isinstance(value, dict):
            result = {key: walk(item) for key, item in value.items()}
            stored = result.get('reference_assets_dir')
            if isinstance(stored, str) and stored and Path(stored).is_absolute():
                try:
                    relative = Path(stored).resolve().relative_to(old_root)
                except ValueError:
                    pass  # Never redirect another recipe's or external assets.
                else:
                    result['reference_assets_dir'] = str(new_root / relative)
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value
    return walk(json.loads(raw))


def write_bytes_atomically(path, data):
    temporary = path.with_suffix(path.suffix + '.rename.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

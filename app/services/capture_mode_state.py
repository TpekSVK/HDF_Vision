"""Persist the operator's mode independently of a camera's power-on default."""
import json
import logging
from pathlib import Path


def load_capture_mode(path):
    try:
        value = json.loads(Path(path).read_text()).get('capture_mode')
        return value if value in ('master', 'trigger') else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def save_capture_mode(path, mode):
    if path is None or mode not in ('master', 'trigger'):
        return
    path = Path(path)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError('Nastavenia aplikácie nie sú objekt.')
        if data.get('capture_mode') == mode:
            return
        data['capture_mode'] = mode
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    except (OSError, ValueError):
        logging.getLogger(__name__).exception('Režim snímania sa nepodarilo uložiť.')

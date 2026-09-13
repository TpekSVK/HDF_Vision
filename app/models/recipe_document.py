"""Pure recipe document validation, shared by parsing and persistence."""
import math
from app.models.recipe_contract import RECIPE_FORMAT_VERSION, RecipeFormatError, validate_tool_contract


def validate_recipe_document(data: object, *, supported_tool_types=None) -> dict:
    def reject(reason):
        raise RecipeFormatError(f"Recept bol odmietnutý: {reason}")

    if not isinstance(data, dict):
        reject("koreň súboru musí byť objekt.")
    version = data.get('format_version')
    if type(version) is not int or version != RECIPE_FORMAT_VERSION:
        reject(f"nepodporovaná verzia {version!r}; požadovaná je {RECIPE_FORMAT_VERSION}. "
               "Staré recepty sa neprevádzajú automaticky. Vytvorte nový recept.")
    if 'tools' in data:
        reject("nástroje musia byť uložené v jednotlivých pohľadoch, nie v koreni receptu.")
    if not isinstance(data.get('regions'), list) or any(not isinstance(r, dict) for r in data['regions']):
        reject("regions musí byť zoznam objektov.")
    views = data.get('views')
    if not isinstance(views, list) or not views:
        reject("chýba neprázdny zoznam pohľadov.")
    if data.get('on_locator_failure') not in ('fail', 'continue_without_alignment'):
        reject("neplatné pravidlo pri zlyhaní zarovnania.")
    for key in ('logging_enabled', 'export_artifacts', 'pose_enabled'):
        if type(data.get(key)) is not bool:
            reject(f"{key} musí byť logická hodnota.")
    aggregation = data.get('aggregation')
    if not isinstance(aggregation, dict) or aggregation.get('mode') not in ('AND', 'OR', 'WEIGHTED'):
        reject("neplatné vyhodnotenie výsledkov pohľadov.")
    ids = set()
    for view in views:
        if not isinstance(view, dict):
            reject("pohľad musí byť objekt.")
        ident = view.get('id')
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            reject("pohľady musia mať jedinečné neprázdne ID.")
        ids.add(ident)
        if not isinstance(view.get('tools'), list):
            reject(f"pohľad {ident}: chýba zoznam nástrojov.")
        for key, allowed in {
            'trigger_mode': {'timed', 'external'},
            'image_rotation': {0, 90, 180, 270},
        }.items():
            value = view.get(key)
            if not isinstance(value, (str, int)) or isinstance(value, bool) or value not in allowed:
                reject(f"pohľad {ident}: neplatné {key}.")
        for key in ('camera_profile',):
            if view.get(key) is not None and not isinstance(view[key], (dict, str)):
                reject(f"pohľad {ident}: neplatné {key}.")
        if type(view.get('branch_enabled')) is not bool:
            reject(f"pohľad {ident}: branch_enabled musí byť logická hodnota.")
        if 'modbus_input' in view:
            reject("staré pole modbus_input už nie je podporované.")
        if view.get('frame_source_view_id') == ident:
            reject(f"pohľad {ident}: nemôže preberať vlastnú snímku.")
        if view['trigger_mode'] == 'external':
            if view.get('external_trigger_mode') not in ('sequential', 'explicit'):
                reject(f"pohľad {ident}: neplatný spôsob externého spúšťania.")
            if view.get('external_source') not in ('pico', 'modbus'):
                reject(f"pohľad {ident}: neplatný zdroj externého spúšťania.")
            if view['external_trigger_mode'] == 'explicit':
                pin = view.get('external_request_input')
                if type(pin) is not int or pin not in range(1, 9):
                    reject(f"pohľad {ident}: požadovaný vstup musí byť číslo 1 až 8.")
        for tool in view['tools']:
            if not isinstance(tool, dict):
                reject(f"pohľad {ident}: nástroj musí byť objekt.")
            kind = tool.get('type')
            if not isinstance(kind, str) or not kind or (supported_tool_types is not None and kind not in supported_tool_types):
                reject(f"neznámy typ nástroja {kind!r}.")
            for key in ('params', 'thresholds'):
                if not isinstance(tool.get(key), dict):
                    reject(f"nástroj {kind}: neplatné {key}.")
            if type(tool.get('enabled')) is not bool or type(tool.get('order')) is not int:
                reject(f"nástroj {kind}: neplatné zapnutie alebo poradie.")
            if not isinstance(tool.get('roi'), dict):
                reject(f"nástroj {kind}: neplatná ROI.")
            params = tool['params']
            validate_tool_contract(kind, params)
    for view in views:
        refs = [view.get('frame_source_view_id'), view.get('branch_default_view_id')]
        targets = view.get('branch_targets', {})
        if not isinstance(targets, dict):
            reject("branch_targets musí byť objekt.")
        refs.extend(targets.values())
        if any(ref is not None and ref != '' and (not isinstance(ref, str) or ref not in ids) for ref in refs):
            reject(f"pohľad {view['id']}: odkaz na neexistujúci pohľad.")

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            reject("číselné hodnoty musia byť konečné.")
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)
    finite(data)
    return data



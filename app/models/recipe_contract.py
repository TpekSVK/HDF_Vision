"""Small, dependency-free rules shared by models, file validation and runners."""
RECIPE_FORMAT_VERSION = 3
REMOVED_TOOL_TYPES = frozenset({'ssd', 'absdiff', 'light_presence', 'template_match'})
LEGACY_LOCATOR_PARAMS = frozenset({
    'rotation_enabled', 'angle_enabled', 'angle_roi', 'angle_method',
    'angle_ref_deg', 'angle_max_dev_deg', 'angle_smooth',
})

class RecipeFormatError(ValueError):
    """The recipe is unsupported or malformed."""


def validate_locator_params(params):
    obsolete = LEGACY_LOCATOR_PARAMS.intersection(params)
    if obsolete:
        raise RecipeFormatError(f"Nepodporované nastavenia zarovnania: {', '.join(sorted(obsolete))}.")
    if params.get('alignment_mode', 'translation') not in ('translation', 'template_rotation', 'guided_edge'):
        raise RecipeFormatError('Nepodporovaný režim zarovnania.')


def validate_tool_contract(kind, params):
    if {'roi', 'ignore_mask', '__roi__'}.intersection(params):
        raise RecipeFormatError('ROI a maska patria do polí nástroja, nie do params.')
    if kind in REMOVED_TOOL_TYPES:
        raise RecipeFormatError(f"Nástroj {kind!r} už nie je podporovaný. Použite aktuálny katalóg.")
    if kind == 'locator.template_match':
        validate_locator_params(params)

from app.models.schema import Tool
from app.utils.tool_identity import compute_tool_identity
from app.utils.tool_labels import tool_display_name


def test_display_translation_preserves_saved_identity():
    t = Tool(type='edge_change', name='Edge Change', order=2)
    identifier, label, order = compute_tool_identity(t)
    assert identifier == '2:Edge Change'
    assert label == 'Plocha rozdielov oproti referencii'
    assert t.name == 'Edge Change'


def test_custom_operator_name_is_preserved():
    t = Tool(type='mse', name='Kontrola ľavého otvoru', order=1)
    assert tool_display_name(t) == t.name

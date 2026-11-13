import pytest
from app.services.tool_registry import ToolRegistry
from app.models.recipe_contract import RecipeFormatError
from app.models.schema import RecipeV2
from app.services.recipe_format import validate_recipe_document


def test_experimental_mold_v2_is_not_registered_or_accepted_in_recipes():
    assert 'mold.protection_v2' not in ToolRegistry.list_tool_types()
    # Removing the experiment must retain the actual production empty-mold tool.
    assert 'mold.protection_v1' in ToolRegistry.list_tool_types()
    doc=RecipeV2().to_dict()
    tool=ToolRegistry.make_default_tool('mold.protection_v1').to_dict()
    tool['type']='mold.protection_v2'
    doc['views'][0]['tools']=[tool]
    with pytest.raises(RecipeFormatError):validate_recipe_document(doc)

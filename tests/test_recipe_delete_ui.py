from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox
from app.services.recipe_service import RecipeService
from app.ui.main_window import MainWindow
import app.ui.main_window as window_module


@pytest.mark.parametrize('decision', ['delete', 'cancel_selection', 'cancel_confirm', 'deny'])
def test_delete_unsupported_without_loading(tmp_path, monkeypatch, decision):
    recipes = RecipeService(tmp_path)
    recipes.create('default')
    recipes.load('default')
    recipes.db.ensure_recipe('old-test')
    old = tmp_path / 'recipes' / 'old-test'
    old.mkdir()
    (old / 'recipe.json').write_text('{"version": 1}')
    other = tmp_path / 'stations' / 'camera_2' / 'recipes' / 'old-test'
    other.mkdir(parents=True)
    (other / 'recipe.json').write_text('untouched')
    with pytest.raises(Exception):
        recipes.load('old-test')
    monkeypatch.setattr(recipes, 'load', Mock(side_effect=AssertionError('must not load')))
    refresh = Mock()
    ui = SimpleNamespace(recipes=recipes, security=object(),
                         current_recipe_name=lambda: 'default', _refresh_recipe_list=refresh)
    def choose(*args):
        assert args[3] == ['old-test']
        assert args[5] is False
        return 'old-test', decision != 'cancel_selection'
    monkeypatch.setattr(QInputDialog, 'getItem', choose)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No if decision == 'cancel_confirm' else QMessageBox.Yes)
    monkeypatch.setattr(window_module, 'authorize_recipe_write', lambda *args: decision != 'deny')
    MainWindow.on_recipe_delete(ui)
    deleted = decision == 'delete'
    assert old.exists() is not deleted
    assert ('old-test' in recipes.list()) is not deleted
    assert refresh.call_count == int(deleted)
    assert recipes.tool.recipe == 'default'
    assert (other / 'recipe.json').read_text() == 'untouched'


@pytest.mark.parametrize('valid_default', [True, False])
def test_active_delete_requires_valid_fallback(tmp_path, monkeypatch, valid_default):
    recipes = RecipeService(tmp_path)
    recipes.create('default')
    recipes.create('active')
    recipes.load('active')
    if not valid_default:
        (tmp_path / 'recipes/default/recipe.json').write_text('{"version": 1}')
    errors = []
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('active', True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[2]))
    monkeypatch.setattr(window_module, 'authorize_recipe_write', lambda *args: True)
    ui = SimpleNamespace(recipes=recipes, security=object(), tool=recipes.tool,
                         current_recipe_name=lambda: 'active', _active_view_id='view_1',
                         setup_recipe_name=Mock())
    for method in ('_refresh_recipe_list', '_persist_last_recipe', '_refresh_views',
                   '_reload_results_strip', '_refresh_tool_selector', '_update_sidebar'):
        setattr(ui, method, Mock())
    MainWindow.on_recipe_delete(ui)
    assert ('active' not in recipes.list()) == valid_default
    assert recipes.tool.recipe == ('default' if valid_default else 'active')
    assert bool(errors) is not valid_default

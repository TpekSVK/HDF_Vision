import json
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from app.services.tool_registry import ToolRegistry
from app.ui.golden_wizard.tool_catalog_dialog import ToolCatalogDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def catalog(base):
    return ToolCatalogDialog(SimpleNamespace(base=base, list_tool_types=ToolRegistry.list_tool_types,
        get_tool_meta=ToolRegistry.get_tool_definition))


def category(dialog, key):
    for row in range(dialog._categories.count()):
        if dialog._categories.item(row).data(Qt.UserRole) == key:
            dialog._categories.setCurrentRow(row)
            return
    pytest.fail('Missing category')


def test_favorites_persist_filter_and_remove_without_adding_tool(app, tmp_path):
    dialog = catalog(tmp_path)
    try:
        category(dialog, '__favorites__')
        assert not dialog._cards
        category(dialog, '__all__')
        card = dialog._cards['ssim']
        card.favorite_checkbox.click()
        assert dialog.selected_type() is None
        assert dialog.result() == 0
        assert json.loads((tmp_path / 'tool_favorites.json').read_text()) == ['ssim']
        category(dialog, '__favorites__')
        assert set(dialog._cards) == {'ssim'}
        dialog._search.setText('no matching tool')
        assert not dialog._cards
        dialog._search.clear()
        dialog._cards['ssim'].clicked.emit('ssim')
        assert dialog.selected_type() == 'ssim'
        reopened = catalog(tmp_path)
        category(reopened, '__favorites__')
        assert reopened._cards['ssim'].favorite_checkbox.isChecked()
        reopened.close()
        dialog._cards['ssim'].favorite_checkbox.click()
        app.processEvents()
        assert not dialog._cards
        assert dialog.selected_type() is None
        assert not dialog._add_button.isEnabled()
        assert json.loads((tmp_path / 'tool_favorites.json').read_text()) == []
    finally:
        dialog.close()


def test_unwritable_favorites_roll_back_checkbox(app, tmp_path, monkeypatch):
    base = tmp_path / 'not_a_directory'
    base.write_text('unchanged')
    notices = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: notices.append(args))
    dialog = catalog(base)
    try:
        category(dialog, '__all__')
        dialog._cards['ssim'].favorite_checkbox.click()
        assert not dialog._cards['ssim'].favorite_checkbox.isChecked()
        assert not dialog._favorites
        assert notices
        assert base.read_text() == 'unchanged'
    finally:
        dialog.close()

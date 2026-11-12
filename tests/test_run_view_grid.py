from PySide6.QtWidgets import QApplication, QScrollArea
from app.ui.run_widgets import RunViewGrid


def test_view_grid_pages_and_preserves_hidden_results():
    app = QApplication.instance() or QApplication([])
    chosen = []
    grid = RunViewGrid(on_view_selected=chosen.append)
    grid.resize(300, 180)
    grid.set_views([{'id': f'view_{i}', 'name': f'View {i}'} for i in range(1, 13)])
    grid.set_active('view_1')
    grid.set_status('view_9', 'nok')
    grid.show(); app.processEvents()
    try:
        assert len([b for b in grid._items.values() if b.isVisible()]) == 4
        assert not grid.findChildren(QScrollArea)
        grid.next.click(); grid.next.click()
        assert grid.page_label.text() == '3 / 3'
        assert grid._items['view_9'].property('status') == 'nok'
        grid._items['view_10'].click()
        assert chosen == ['view_10']
        # Changing the visible page does not change the selected runtime view.
        assert grid._active == 'view_1'
        grid.set_active('view_10')
        assert grid._items['view_10'].isChecked()
        grid.set_active('view_2')
        assert grid.page_label.text() == '1 / 3'
        grid.set_views([{'id':f'view_{i}', 'name':f'View {i}'} for i in range(1, 5)])
        assert not grid.pager.isVisible()
        assert len([b for b in grid._items.values() if b.isVisible()]) == 4
        assert grid.view_ids() == ['view_1','view_2','view_3','view_4']
    finally:
        grid.close()
        app.processEvents()

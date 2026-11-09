"""Real Qt regressions for the touch Golden workspace and navigation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from app.services.recipe_service import RecipeService
from app.services.storage_service import save_golden
from app.services.tool_registry import ToolRegistry
from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.image_canvas import InteractionMode
from app.ui.roi_mask_editor import LocatorROIEditor


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wizard(app, tmp_path):
    recipes = RecipeService(tmp_path)
    recipes.create('default')
    save_golden(np.full((1944, 2592), 120, np.uint8), 'default', base_dir=tmp_path)
    recipes.load('default')
    for kind in sorted(ToolRegistry.list_tool_types(), key=lambda name: not name.startswith("locator.")):
        tool = ToolRegistry.make_default_tool(kind)
        tool.roi = tool.roi.from_obj({'x': 500, 'y': 600, 'w': 900, 'h': 800})
        recipes.add_tool('default', tool, view_id='view_1')
    camera = SimpleNamespace(width=2592, height=1944, fps=30, pixel_format='Y8', exposure_us=1000)
    w = GoldenWizard(camera, recipes, get_capture_mode=lambda: 'master')
    w.setWindowState(Qt.WindowNoState)
    w.resize(1366, 768)
    w.show()
    app.processEvents()
    yield w
    w._has_unsaved_changes = lambda: False
    w._dirty_views = {}
    w.reject()
    recipes.db.close()
    w.deleteLater()
    app.processEvents()


def test_full_workspace_and_all_tool_forms_fit_without_scroll(app, wizard):
    w = wizard
    if os.environ.get('HDF_TOUCH_SCREENSHOT'):
        w.grab().save('/testdeps/golden-touch-initial.png')
    assert w.width() == 1366
    assert w.height() == 768
    assert not w.tools_table.isVisible()
    before = w._canvas_panel.geometry()
    w._more_button.click(); app.processEvents()
    assert w._options_overlay.isVisible()
    assert before == w._canvas_panel.geometry()
    w._options_overlay.hide()
    for row in range(w.tools_table.rowCount()):
        w._select_touch_tool(row); app.processEvents()
        assert w._touch_inspector.isVisible()
        groups = w._touch_inspector.groups
        for index in range(groups.count()):
            groups.setCurrentIndex(index); app.processEvents()
            pager = w._touch_inspector
            while True:
                assert pager.body.geometry().bottom() < pager.height()
                content = pager.body.currentWidget()
                assert content.minimumSizeHint().height() <= pager.body.height(), (row, groups.currentText(), pager.page, content.minimumSizeHint(), pager.body.size())
                if not pager.next.isEnabled(): break
                pager.next.click(); app.processEvents()
        # The complete footer remains on screen for every tool and group.
        assert w.btn_publish_recipe.mapTo(w, QPoint()).y() + w.btn_publish_recipe.height() <= 768
    assert w.roi_editor._view.viewport().width() >= 850


def test_copy_reorder_and_confirmed_delete_keep_draft(app, wizard, monkeypatch):
    w = wizard
    count = w.tools_table.rowCount()
    w._select_touch_tool(count - 1)
    old = w.recipes.get_draft_tools('default', 'view_1')[-1].to_dict()
    w._btn_copy_selected.click(); app.processEvents()
    assert w.tools_table.rowCount() == count + 1
    copy = w.recipes.get_draft_tools('default', 'view_1')[w.tools_table.currentRow()]
    assert copy.roi.to_dict() == old['roi']
    row = w.tools_table.currentRow()
    w._btn_tool_up.click(); app.processEvents()
    assert w.tools_table.currentRow() == row - 1
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    w._btn_delete_selected.click()
    assert w.tools_table.rowCount() == count + 1
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    w._btn_delete_selected.click(); app.processEvents()
    assert w.tools_table.rowCount() == count


@pytest.fixture
def editor(app):
    e = LocatorROIEditor()
    e.enable_touch_controls()
    pixmap = QPixmap(1600, 1200); pixmap.fill(Qt.gray)
    e.set_background(pixmap)
    e.set_roi((500, 300, 500, 500))
    e.resize(1000, 700); e.show(); app.processEvents()
    yield e
    e.close(); e.deleteLater(); app.processEvents()


def test_polygon_buttons_commit_roi_and_ignore_mask(app, editor):
    e, v = editor, editor._view
    e._start_shape_draw('polygon')
    e._touch.sync()
    for p in (QPoint(200, 200), QPoint(400, 200), QPoint(400, 400)):
        QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    e._touch.sync()
    assert e._touch.finish.isEnabled()
    e._touch.back.click(); assert len(v._draft_points) == 2
    QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(400, 400))
    app.processEvents()
    assert len(v._draft_points) == 3, (v._draw_shape, v.interaction_mode(), v._draft_points)
    assert e._touch.finish.isEnabled()
    e._touch.finish.click()
    assert e.roi_data()['shape'] == 'polygon'
    assert len(e.roi_data()['points']) == 3
    e.configure_ignore_mask(True)
    e.set_edit_context('mask'); e.set_mask_tool('polygon')
    e._touch.sync()
    for p in (QPoint(200, 200), QPoint(400, 200), QPoint(400, 400)):
        QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    e._touch.sync()
    app.processEvents()
    e._touch.finish.click()
    assert e._view.mask().any()
    assert not v._mask_polygon


@pytest.mark.parametrize("context", ["roi", "mask", "edge"])
def test_two_finger_zoom_pan_preserves_roi_mask_and_edge(app, editor, context):
    e, v = editor, editor._view
    e.configure_ignore_mask(True)
    e.configure_edge_anchors(True, (600, 500), (900, 500), 20)
    e.set_edit_context(context)
    if context == 'mask': e.set_mask_tool('brush')
    e._touch.sync()
    app.processEvents()
    device = QTest.createTouchDevice()
    sequence = QTest.touchEvent(v.viewport(), device, False)
    original = e.roi_data()
    original_mask = v.mask().copy()
    original_edges = v.edge_points()
    zoom = v.zoom()
    # Real touch events go through QGraphicsView's viewportEvent, not mouse emulation.
    sequence.press(0, QPoint(300, 250), v.viewport()).commit()
    sequence.stationary(0).press(1, QPoint(500, 250), v.viewport()).commit()
    sequence.move(0, QPoint(250, 300), v.viewport()).move(1, QPoint(650, 300), v.viewport()).commit()
    app.processEvents()
    assert v.zoom() == pytest.approx(zoom * 2, rel=.03)
    anchor = v.mapToScene(QPoint(450, 300))
    sequence.move(0, QPoint(330, 340), v.viewport()).move(1, QPoint(730, 340), v.viewport()).commit()
    app.processEvents()
    moved_anchor = v.mapToScene(QPoint(530, 340))
    assert (moved_anchor - anchor).manhattanLength() < 8 / v.zoom()
    sequence.release(0, QPoint(330, 340), v.viewport()).release(1, QPoint(730, 340), v.viewport()).commit()
    assert e.roi_data() == original
    np.testing.assert_array_equal(v.mask(), original_mask)
    assert v.edge_points() == original_edges


def test_second_finger_rolls_back_vertex_drag_and_does_not_commit_when_one_finger_remains(app, editor):
    e, v = editor, editor._view
    e.set_roi_data({'shape':'polygon', 'points':[[400,400],[1000,400],[1000,1000]]})
    e._touch.sync(); app.processEvents()
    v.set_zoom(.6)
    original = e.roi_data()
    point = v.mapFromScene(QPointF(400,400))
    device = QTest.createTouchDevice()
    sequence = QTest.touchEvent(v.viewport(), device, False)
    sequence.press(0, point, v.viewport()).commit()
    sequence.move(0, point + QPoint(50,30), v.viewport()).commit()
    assert e.roi_data() != original
    sequence.stationary(0).press(1, point + QPoint(200,0), v.viewport()).commit()
    sequence.stationary(0).release(1, point + QPoint(200,0), v.viewport()).commit()
    sequence.move(0, point + QPoint(80,80), v.viewport()).commit()
    sequence.release(0, point + QPoint(80,80), v.viewport()).commit()
    assert e.roi_data() == original
    assert not v.can_undo()


def test_locator_selection_changes_only_through_area_buttons(app, editor):
    e, v = editor, editor._view
    search = dict(x=100, y=100, w=1300, h=1000)
    template = dict(x=500, y=400, w=300, h=300)
    e.set_locator_mode(True, search=search, template=template)
    e._touch.sync(); app.processEvents()
    e._touch.area_buttons['template'].click()
    assert e._active_area == 'template'
    QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, v.mapFromScene(QPointF(200, 200)))
    assert e._active_area == 'template'
    e._touch.area_buttons['search'].click()
    QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, v.mapFromScene(QPointF(650, 550)))
    assert e._active_area == 'search'
    assert e._touch.area_buttons['search'].isChecked()
    assert not e._touch.area_buttons['template'].isChecked()


def test_five_tools_fit_and_last_page_keeps_selection(app, wizard):
    w = wizard
    w._show_touch_panel(0); app.processEvents()
    pages = w._tool_pages
    assert sum(button.isVisible() for button in pages.buttons) == 5
    assert pages.label.text() == '1 / 3'
    for button in pages.buttons:
        assert button.height() >= 44
        assert button.mapTo(w, QPoint()).y() + button.height() < w.btn_add_tool.mapTo(w, QPoint()).y()
    assert w._btn_tool_enabled.mapTo(w, QPoint()).y() + w._btn_tool_enabled.height() < w.btn_close_wizard.mapTo(w, QPoint()).y()
    pages.next.click(); app.processEvents()
    pages.buttons[4].click(); app.processEvents()
    assert w.tools_table.currentRow() == 9
    w._show_touch_panel(0)
    assert pages.label.text() == '2 / 3'
    pages.next.click(); app.processEvents()
    assert sum(button.isVisible() for button in pages.buttons) == 1


def test_result_is_one_compact_page_with_metrics(app, wizard):
    from app.services.tool_contracts import ToolRunResult
    w = wizard
    w._select_touch_tool(0)
    p, pager = w._tool_panel, w._touch_inspector
    p.show_test_result(ToolRunResult(status='ok', latency_ms=12, metrics={'corr':.99, 'dx':2, 'dy':1}), 12)
    pager.refresh()
    pager.groups.setCurrentIndex(pager.groups.findData(6)); app.processEvents()
    assert pager.page_label.text() == '1 / 1'
    assert not pager.next.isEnabled()
    assert p._status_value_label.isVisible()
    assert p._latency_label.isVisible()
    assert p._metrics_table.isVisible()
    assert p._metrics_table.rowCount() >= 3
    assert p._status_value_label.mapTo(pager.body, QPoint()).y() < 50
    assert p._metrics_table.mapTo(pager.body, QPoint()).y() < 100
    if os.environ.get('HDF_TOUCH_SCREENSHOT'):
        w.grab().save('/testdeps/golden-touch-result.png')


def test_combo_popup_has_dark_background(app, wizard):
    w = wizard
    w._show_touch_panel(1); app.processEvents()
    for combo in (w._view_selector, w.roi_editor._touch.context, w.roi_editor._touch.tool, w._touch_inspector.groups):
        combo.showPopup(); app.processEvents()
        view = combo.view()
        popup = view.window()
        assert popup.width() >= 240
        for row in range(combo.count()):
            assert view.visualRect(view.model().index(row,0)).height() >= 48
        # Inspect the entire popup, including its native outer frame, rather
        # than just the list viewport (which missed the old white border).
        image = popup.grab().toImage()
        for x,y in ((0,0),(image.width()-1,0),(0,image.height()-1),(image.width()-1,image.height()-1)):
            color = image.pixelColor(x,y)
            assert max(color.red(),color.green(),color.blue()) < 180, (combo.accessibleName(),color.name())
        if os.environ.get('HDF_TOUCH_SCREENSHOT') and combo is w.roi_editor._touch.tool:
            popup.grab().save('/testdeps/golden-touch-popup.png')
        row = min(1,combo.count()-1)
        target = view.visualRect(view.model().index(row,0)).center()
        QTest.mouseClick(view.viewport(),Qt.LeftButton,Qt.NoModifier,target); app.processEvents()
        assert combo.currentIndex()==row
        combo.hidePopup(); app.processEvents()


@pytest.mark.parametrize('kind,invalid', [('mold.protection_v1',False), ('mold.protection_v1',True), ('presence.absence_v2',False)])
def test_learning_actions_fit_on_one_page_and_keep_signals(app, wizard, kind, invalid):
    w = wizard
    tools = w.recipes.get_draft_tools('default','view_1')
    row = next(i for i,t in enumerate(tools) if t.type == kind)
    w._select_touch_tool(row)
    p, pager = w._tool_panel, w._touch_inspector
    tool = p._current_tool
    tool.params.values.update(reference_model_invalidated=invalid, reference_model_ready=not invalid,
                              sample_count_ok=30, sample_count_nok=5)
    p.refresh_presence_learning(tool, compatible_ok=30, nok_count=5,
                               recommended={'score_threshold':4, 'total_area_threshold':50, 'min_blob_area':10})
    pager.refresh()
    pager.groups.setCurrentIndex(pager.groups.findData(3)); app.processEvents()
    assert pager.page_label.text() == '1 / 1'
    assert not pager.next.isEnabled()
    assert p._presence_model_state.isVisible()
    assert p._presence_counts.isVisible()
    assert p._presence_warning.isVisible() == invalid
    assert p._shared_mold_ok.isVisible() == (kind == 'mold.protection_v1')
    content = pager.learning_page
    assert content.minimumSizeHint().height() <= pager.body.height()
    for button in [*p._presence_capture_buttons.values(), p._presence_rebuild, content.details]:
        assert button.isVisible()
        assert button.height() >= 44
        assert button.mapTo(content, QPoint()).y() + button.height() <= content.height()
    # Check the same live buttons still dispatch the established actions, without
    # starting a hardware capture from this layout test.
    p.presenceLearningRequested.disconnect()
    actions = []
    p.presenceLearningRequested.connect(actions.append)
    p._presence_capture_buttons['capture_ok'].click()
    p._presence_capture_buttons['capture_nok'].click()
    p._presence_rebuild.click()
    if kind == 'mold.protection_v1': p._shared_mold_ok.click()
    assert actions[:3] == ['capture_ok','capture_nok','rebuild']
    content.details.click(); app.processEvents()
    assert content.overlay.isVisible()
    assert p._presence_recommended.isVisible()
    assert p._presence_reset.isVisible()
    p._presence_reset.click()
    assert actions[-1] == 'reset'
    content.overlay.hide()
    if os.environ.get('HDF_TOUCH_SCREENSHOT'):
        w.grab().save('/testdeps/golden-touch-learning.png')


@pytest.mark.parametrize('mode', ['good', 'weak', 'missing_nok', 'tuning', 'overlap'])
def test_validation_actions_and_reports_share_one_page(app, wizard, mode):
    w = wizard
    tools = w.recipes.get_draft_tools('default', 'view_1')
    w._select_touch_tool(next(i for i,t in enumerate(tools) if t.type == 'mold.protection_v1'))
    p, pager = w._tool_panel, w._touch_inspector
    page = pager.validation_page
    pager.groups.setCurrentIndex(pager.groups.findData(4))
    summary = dict(ok_total=30, nok_total=10, ok_correct=30, nok_correct=10,
                   false_reject_count=0, false_accept_count=0,
                   false_reject_rate=0., false_accept_rate=0., accuracy=1.)
    if mode == 'missing_nok': summary.update(nok_total=0, nok_correct=0)
    p.show_presence_validation(summary, weak_dataset=mode=='weak')
    tuning = mode in ('tuning', 'overlap')
    if tuning:
        before = {**summary,'false_reject_count':6,'false_reject_rate':.2}
        after = dict(summary)
        if mode == 'overlap': after.update(false_accept_count=2, false_accept_rate=.2)
        p.show_presence_tuning(dict(current_validation_summary=before,
            recommended_validation_summary=after,has_nok_samples=True,
            recommended_sensitivity=65,recommended_thresholds=dict(total_area_threshold=50,min_blob_area=10)),
            weak_dataset=mode=='overlap')
    app.processEvents()
    assert pager.page_label.text() == '1 / 1'
    assert not pager.next.isEnabled()
    assert page.minimumSizeHint().height() <= pager.body.height()
    assert page.warning.isVisible() == (mode in ('weak','missing_nok','overlap'))
    if tuning:
        assert '65 %' in page.summary.text()
        assert '20.0 → 0.0 %' in page.summary.text()
    else:
        assert '30 / 30' in page.summary.text()
    assert p._presence_apply_tuned.isVisible() == tuning
    for button in (p._presence_validate_button,p._presence_auto_settings_button,page.details):
        assert button.isVisible() and button.height() >= 44
        assert button.mapTo(page,QPoint()).y()+button.height() <= page.height()
    p.presenceLearningRequested.disconnect()
    actions=[]
    p.presenceLearningRequested.connect(actions.append)
    p._presence_validate_button.click()
    p._presence_auto_settings_button.click()
    if tuning: p._presence_apply_tuned.click()
    assert actions == ['validate','auto_settings']+(['apply_auto_settings'] if tuning else [])
    page.details.click(); app.processEvents()
    assert page.overlay.isVisible()
    assert page.reports.currentWidget().isVisible()
    assert page.reports.currentWidget().minimumSizeHint().height() <= page.reports.height()
    if tuning:
        page.validation_tab.click(); app.processEvents()
        assert p._presence_validation_result.isVisible()
        assert not p._presence_tuning_result.isVisible()
        page.tuning_tab.click(); app.processEvents()
        assert p._presence_tuning_result.isVisible()
    page.overlay.hide()
    if os.environ.get('HDF_TOUCH_SCREENSHOT'):
        w.grab().save('/testdeps/golden-touch-validation-'+mode+'.png')


def test_tool_single_click_selects_and_double_click_or_tab_opens_parameters(app, wizard):
    w = wizard
    w._show_touch_panel(0)
    cards = w._tool_pages.buttons
    QTest.mouseClick(cards[1], Qt.LeftButton); app.processEvents()
    assert w.tools_table.currentRow() == 1
    assert cards[1].isChecked()
    assert w._touch_stack.currentIndex() == 0
    # Clicking an already selected, checkable card must keep it selected.
    QTest.mouseClick(cards[1], Qt.LeftButton); app.processEvents()
    assert cards[1].isChecked()
    assert w._touch_stack.currentIndex() == 0
    w._parameters_tab.click(); app.processEvents()
    assert w._touch_stack.currentIndex() == 1
    assert w._tool_panel._name_input.text() == w.tools_table.item(1,1).text()
    w._tools_tab.click(); app.processEvents()
    w._tool_pages.next.click(); app.processEvents()
    QTest.mouseClick(cards[2], Qt.LeftButton); app.processEvents()
    assert w.tools_table.currentRow() == 7
    assert w._touch_stack.currentIndex() == 0
    QTest.mouseDClick(cards[2], Qt.LeftButton); app.processEvents()
    assert w._touch_stack.currentIndex() == 1
    assert w.tools_table.currentRow() == 7
    assert w._tool_panel._name_input.text() == w.tools_table.item(7,1).text()


@pytest.mark.parametrize('mode,pages', [('translation',1), ('template_rotation',1), ('guided_edge',3)])
def test_locator_alignment_groups_related_fields_and_keeps_values(app, wizard, mode, pages):
    w = wizard
    w._select_touch_tool(0)
    p, pager = w._tool_panel, w._touch_inspector
    mode_widget = p._param_widgets['alignment_mode']
    mode_widget.setCurrentIndex(mode_widget.findData(mode)); app.processEvents()
    pager.groups.setCurrentIndex(pager.groups.findData(1)); app.processEvents()
    assert pager.page_label.text().startswith(f'1 / {pages}')
    seen = set()
    page_count = 0
    while True:
        app.processEvents()
        assert p._form_container.minimumSizeHint().height() <= pager.body.height()
        for name, wrapper in p._param_wrappers.items():
            if wrapper.isVisible():
                seen.add(name)
                assert wrapper.mapTo(pager.body,QPoint()).y()+wrapper.height() <= pager.body.height()
        page_count += 1
        if not pager.next.isEnabled(): break
        pager.next.click()
    assert page_count == pages
    expected = {'use_golden_crop','alignment_mode'}
    if mode == 'guided_edge':
        expected.update({'reference_search_half_window','reference_edge_polarity','reference_grad_threshold',
                         'reference_blur_sigma','reference_scan_step','reference_min_coverage',
                         'reference_use_subpixel','reference_max_angle_deg'})
    assert seen == expected
    # These are the existing form widgets, with the existing persistence path.
    if mode == 'guided_edge':
        p._param_widgets['reference_scan_step'].setValue(3); app.processEvents()
        assert w.recipes.get_draft_tools('default','view_1')[0].params.values['reference_scan_step'] == 3
        pager.previous.click(); pager.previous.click(); app.processEvents()
        mode_widget.setCurrentIndex(mode_widget.findData('translation')); app.processEvents()
        assert pager.page_label.text() == '1 / 1'
        assert not p._param_wrappers['reference_scan_step'].isVisible()
    if os.environ.get('HDF_TOUCH_SCREENSHOT'):
        w.grab().save('/testdeps/golden-touch-alignment-'+mode+'.png')


@pytest.mark.parametrize('tested', [False,True])
def test_mold_sensitivity_has_two_groups_and_keeps_last_result(app, wizard, tested):
    from app.services.tool_contracts import ToolRunResult
    w = wizard
    tools = w.recipes.get_draft_tools('default','view_1')
    row = next(i for i,t in enumerate(tools) if t.type == 'mold.protection_v1')
    w._select_touch_tool(row)
    p, pager = w._tool_panel, w._touch_inspector
    pager.groups.setCurrentIndex(pager.groups.findData(2))
    if tested:
        p.show_test_result(ToolRunResult(status='nok',latency_ms=12,metrics={
            'inspection_state':'occupied','residual_count':2,'largest_blob_area':200}),12)
    for page, expected in enumerate(({'sensitivity','polarity','min_blob_area'},
                                     {'max_largest_blob_area','max_blob_count','total_area_threshold'})):
        app.processEvents()
        assert pager.page_label.text().startswith(f'{page+1} / 2')
        wrappers = {**p._threshold_wrappers,'polarity':p._param_wrappers['polarity']}
        assert {name for name,wrapper in wrappers.items() if wrapper.isVisible()} == expected
        assert p._presence_live_values.isVisible()
        if tested: assert 'Počet zvyškov: 2' in p._presence_live_values.text()
        assert p._threshold_container.minimumSizeHint().height() <= pager.body.height()
        for name in expected:
            wrapper=wrappers[name]
            assert wrapper.mapTo(pager.body,QPoint()).y()+wrapper.height() <= pager.body.height()
        if os.environ.get('HDF_TOUCH_SCREENSHOT'):
            w.grab().save(f'/testdeps/golden-touch-sensitivity-{page}.png')
        if page==0: pager.next.click()
    assert not pager.next.isEnabled()
    p._threshold_widgets['max_blob_count'].setValue(3); app.processEvents()
    assert w.recipes.get_draft_tools('default','view_1')[row].thresholds.values['max_blob_count']==3


def test_mold_advanced_groups_capture_and_model_without_losing_settings(app, wizard):
    w = wizard
    tools = w.recipes.get_draft_tools('default','view_1')
    row = next(i for i,t in enumerate(tools) if t.type == 'mold.protection_v1')
    w._select_touch_tool(row)
    p, pager = w._tool_panel, w._touch_inspector
    pager.groups.setCurrentIndex(pager.groups.findData(5))
    seen = set()
    for page, expected in enumerate(({'capture_mode_default','min_ok_samples','recommended_ok_samples','use_nok_for_validation'},
                                     {'expected_state','reference_mode','model_method'})):
        app.processEvents()
        assert pager.page_label.text().startswith(f'{page+1} / 2')
        visible={name for name,wrapper in p._param_wrappers.items() if wrapper.isVisible()}
        assert visible == expected
        seen.update(visible)
        assert p._advanced_container.minimumSizeHint().height() <= pager.body.height()
        for name in visible:
            wrapper = p._param_wrappers[name]
            assert wrapper.mapTo(pager.body,QPoint()).y()+wrapper.height() <= pager.body.height()
        if os.environ.get('HDF_TOUCH_SCREENSHOT'):
            w.grab().save(f'/testdeps/golden-touch-mold-advanced-{page}.png')
        if page==0: pager.next.click()
    assert len(seen)==7
    pager.previous.click(); app.processEvents()
    p._param_widgets['min_ok_samples'].setValue(20)
    p._param_widgets['recommended_ok_samples'].setValue(40)
    p._param_widgets['use_nok_for_validation'].setChecked(False)
    app.processEvents()
    saved=w.recipes.get_draft_tools('default','view_1')[row].params.values
    assert saved['min_ok_samples']==20
    assert saved['recommended_ok_samples']==40
    assert saved['use_nok_for_validation'] is False


def test_wizard_reports_unexecuted_ssim_and_actual_failed_locator_separately(app, wizard):
    w=wizard
    w._current_locator_failure_policy='fail'
    captures=[]
    def capture(**kwargs):
        captures.append(kwargs)
        return w._current_golden_image().copy()
    w._capture_frame_for_golden=capture
    tools=w.recipes.get_draft_tools('default','view_1')
    tools[0].params.values['alignment_mode']='guided_edge'
    w.recipes.update_tool('default',0,tools[0],view_id='view_1')
    row=next(i for i,t in enumerate(tools) if t.type=='ssim')
    w._select_touch_tool(row)
    w._tool_panel.trigger_test(); app.processEvents()
    p=w._tool_panel
    assert p._status_value_label.text()=='NEOTESTOVANÉ'
    assert 'chýba oblasť šablóny' in p._status_message_label.text()
    assert not p._metrics_table.isVisible()
    assert 'Test zlyhal' not in p._status_message_label.text()
    assert captures[-1]['view_id']=='view_1'
    assert captures[-1]['capture_request_source']=='tool_test'
    w._select_touch_tool(0)
    p.trigger_test(); app.processEvents()
    assert p._status_value_label.text()=='NOK'
    assert 'Test zlyhal' not in p._status_message_label.text()


def test_locator_acceptance_limits_fit_together_and_persist(app, wizard):
    w = wizard
    w._select_touch_tool(0)
    p, pager = w._tool_panel, w._touch_inspector
    pager.groups.setCurrentIndex(pager.groups.findData(2)); app.processEvents()
    assert pager.page_label.text() == '1 / 1'
    assert not pager.previous.isEnabled() and not pager.next.isEnabled()
    assert set(p._threshold_widgets) == {'threshold_corr','max_shift_x','max_shift_y'}
    assert p._threshold_container.minimumSizeHint().height() <= pager.body.height()
    for name, wrapper in p._threshold_wrappers.items():
        assert wrapper.isVisible(), name
        assert wrapper.mapTo(pager.body,QPoint()).y()+wrapper.height() <= pager.body.height()
        assert p._threshold_widgets[name].height() >= 44
    for name, value in [('threshold_corr',.8),('max_shift_x',120),('max_shift_y',90)]:
        p._threshold_widgets[name].setValue(value)
    app.processEvents()
    saved = w.recipes.get_draft_tools('default','view_1')[0].thresholds.values
    assert saved['threshold_corr'] == pytest.approx(.8)
    assert saved['max_shift_x'] == 120
    assert saved['max_shift_y'] == 90
    assert pager.page_label.text() == '1 / 1'
    if os.environ.get('HDF_TOUCH_SCREENSHOT'):
        w.grab().save('/testdeps/golden-touch-acceptance.png')

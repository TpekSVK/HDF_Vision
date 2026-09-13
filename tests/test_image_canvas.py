"""Real Qt event tests; run on Jetson with QT_QPA_PLATFORM=offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QFocusEvent, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from app.ui.image_canvas import InteractionMode
from app.ui.roi_mask_editor import ROIEditor, MaskEditor


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qt_app):
    widget = ROIEditor()
    widget.resize(700, 500)
    pixmap = QPixmap(1600, 1200)
    pixmap.fill(Qt.darkGray)
    widget.set_background(pixmap)
    widget.set_roi((500, 300, 200, 100))
    widget.show()
    qt_app.processEvents()
    widget._view.setFocus()
    yield widget
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def move(view, point, buttons=Qt.LeftButton):
    event = QMouseEvent(QEvent.MouseMove, QPointF(point),
                        QPointF(view.viewport().mapToGlobal(point)),
                        Qt.NoButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), event)


def drag(view, start, end, button=Qt.LeftButton):
    QTest.mousePress(view.viewport(), button, Qt.NoModifier, start)
    move(view, end, button)
    QTest.mouseRelease(view.viewport(), button, Qt.NoModifier, end)


def wheel(view, position, delta=120, pixel_delta=0):
    event = QWheelEvent(QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                        QPoint(0, pixel_delta), QPoint(0, delta), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), event)


def test_select_default_does_not_replace_roi(editor):
    view = editor._view
    assert view.interaction_mode() == InteractionMode.SELECT
    drag(view, QPoint(100, 100), QPoint(220, 200))
    assert editor.roi() == (500, 300, 200, 100)
    assert not view.can_undo()


@pytest.mark.parametrize("mode", list(InteractionMode))
def test_toolbar_mode_switch(editor, mode):
    button = editor._shape_buttons[0] if mode == InteractionMode.DRAW else editor._navigation.mode_buttons[mode]
    QTest.mouseClick(button, Qt.LeftButton)
    assert editor._view.interaction_mode() == mode
    assert button.isChecked()
    assert sum(b.isChecked() for b in editor._navigation._modes.buttons()) == 1


def test_draw_completes_in_image_coordinates_and_returns_to_select(editor):
    view = editor._view
    view.reset_zoom_100()
    view.centerOn(700, 500)
    view.set_interaction_mode(InteractionMode.DRAW)
    start = view.mapFromScene(QPointF(500, 300))
    end = view.mapFromScene(QPointF(750, 450))
    drag(view, start, end)
    assert editor.roi() == (500, 300, 250, 150)
    assert view.interaction_mode() == InteractionMode.SELECT
    view.undo()
    assert editor.roi() == (500, 300, 200, 100)
    view.redo()
    assert editor.roi() == (500, 300, 250, 150)


@pytest.mark.parametrize("initial", [(500, 300, 200, 100), None])
def test_escape_discards_preview_preserves_saved_roi(editor, initial):
    view = editor._view
    editor.set_roi(initial)
    view.set_interaction_mode(InteractionMode.DRAW)
    start = view.mapFromScene(QPointF(600, 400))
    end = view.mapFromScene(QPointF(850, 650))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    move(view, end)
    assert view._drawing
    QTest.keyClick(view, Qt.Key_Escape)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    assert editor.roi() == initial
    assert not view._drawing
    assert not view.can_undo()
    assert view.interaction_mode() == InteractionMode.SELECT
    if initial is None:
        assert view._roi_item is None
    else:
        assert view._roi_item.path().boundingRect() == QRectF(*initial)


def test_escape_does_not_close_parent_dialog(qt_app):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    widget = ROIEditor(dialog)
    layout.addWidget(widget)
    dialog.show()
    qt_app.processEvents()
    widget._view.set_interaction_mode(InteractionMode.DRAW)
    widget._view.setFocus()
    QTest.keyClick(widget._view, Qt.Key_Escape)
    assert dialog.isVisible()
    assert widget._view.interaction_mode() == InteractionMode.SELECT
    dialog.close()
    dialog.deleteLater()


def test_zoom_clamp_and_actual_size(editor):
    view = editor._view
    view.set_zoom(0.0001)
    assert view.zoom() == pytest.approx(0.1)
    view.set_zoom(100)
    assert view.zoom() == pytest.approx(48)
    QTest.mouseClick(editor._navigation.actual_size_button, Qt.LeftButton)
    assert view.zoom() == pytest.approx(1)
    assert editor._navigation.zoom_label.text() == "100 %"


def test_toolbar_keyboard_fit_and_zoom_label(editor):
    view = editor._view
    bar = editor._navigation
    QTest.mouseClick(bar.actual_size_button, Qt.LeftButton)
    QTest.mouseClick(bar.zoom_in_button, Qt.LeftButton)
    assert view.zoom() == pytest.approx(1.25)
    assert bar.zoom_label.text() == "125 %"
    QTest.mouseClick(bar.zoom_out_button, Qt.LeftButton)
    assert view.zoom() == pytest.approx(1)
    for key in (Qt.Key_Equal, Qt.Key_Plus):
        QTest.keyClick(view, key)
    assert view.zoom() == pytest.approx(1.5625)
    QTest.keyClick(view, Qt.Key_Minus)
    assert view.zoom() == pytest.approx(1.25)
    QTest.keyClick(view, Qt.Key_1)
    assert view.zoom() == pytest.approx(1)
    QTest.keyClick(view, Qt.Key_F)
    assert view.zoom() < 1
    assert bar.zoom_label.text() == f"{view.zoom() * 100:.0f} %"
    image_rect = view.mapFromScene(view.scene_rect()).boundingRect()
    assert view.viewport().rect().contains(image_rect)
    assert view.transform().m11() == pytest.approx(view.transform().m22())


@pytest.mark.parametrize("pixel", [False, True])
def test_wheel_keeps_cursor_image_position(editor, pixel):
    view = editor._view
    view.reset_zoom_100()
    view.centerOn(800, 600)
    point = QPoint(230, 190)
    before = view.mapToScene(point)
    wheel(view, point, delta=0 if pixel else 120, pixel_delta=15 if pixel else 0)
    assert view.zoom() == pytest.approx(1.25)
    assert (view.mapToScene(point) - before).manhattanLength() < 2
    wheel(view, point, delta=-120)
    assert view.zoom() == pytest.approx(1)
    assert editor._navigation.zoom_label.text() == "100 %"


@pytest.mark.parametrize("gesture", ["explicit", "space", "middle"])
def test_pan_never_draws_and_restores_mode(editor, gesture):
    view = editor._view
    view.reset_zoom_100()
    view.centerOn(800, 600)
    view.set_interaction_mode(InteractionMode.PAN if gesture == "explicit" else InteractionMode.DRAW)
    expected_mode = view.interaction_mode()
    if gesture == "space":
        QTest.keyPress(view, Qt.Key_Space)
        assert view.cursor().shape() == Qt.OpenHandCursor
    button = Qt.MiddleButton if gesture == "middle" else Qt.LeftButton
    before = view.mapToScene(QPoint(300, 250))
    QTest.mousePress(view.viewport(), button, Qt.NoModifier, QPoint(300, 250))
    assert view.cursor().shape() == Qt.ClosedHandCursor
    move(view, QPoint(360, 290), button)
    QTest.mouseRelease(view.viewport(), button, Qt.NoModifier, QPoint(360, 290))
    if gesture == "space":
        QTest.keyRelease(view, Qt.Key_Space)
    assert view.interaction_mode() == expected_mode
    assert not view._panning
    assert editor.roi() == (500, 300, 200, 100)
    assert not view.can_undo()
    after = view.mapToScene(QPoint(300, 250))
    assert after.x() == pytest.approx(before.x() - 60, abs=1)
    assert after.y() == pytest.approx(before.y() - 40, abs=1)


def test_navigation_resize_and_tab_fit_preserve_roi(editor, qt_app):
    view = editor._view
    view.set_zoom(2)
    view.centerOn(650, 500)
    before = view.mapToScene(view.viewport().rect().center())
    editor.resize(850, 650)
    qt_app.processEvents()
    view.schedule_fit_to_view(source="tab_roi:currentChanged")
    qt_app.processEvents()
    assert view.zoom() == pytest.approx(2)
    after = view.mapToScene(view.viewport().rect().center())
    assert (after - before).manhattanLength() < 2
    view.fit_image_to_view()
    assert editor.roi() == (500, 300, 200, 100)


def test_initial_fit_follows_layout_until_manual_navigation(editor, qt_app):
    view = editor._view
    before = view.zoom()
    editor.resize(950, 750)
    qt_app.processEvents()
    qt_app.processEvents()
    assert view.zoom() > before
    assert view.viewport().rect().contains(view.mapFromScene(view.scene_rect()).boundingRect())
    view.reset_zoom_100()
    editor.resize(700, 500)
    qt_app.processEvents()
    assert view.zoom() == pytest.approx(1)


def test_manual_zoom_cancels_queued_initial_fit(qt_app):
    widget = ROIEditor()
    pixmap = QPixmap(1200, 900)
    pixmap.fill(Qt.gray)
    widget.set_background(pixmap)
    widget._view.set_zoom(2)
    widget.show()
    qt_app.processEvents()
    assert widget._view.zoom() == pytest.approx(2)
    widget.close()
    widget.deleteLater()


def test_focus_loss_cancels_temporary_pan(editor):
    view = editor._view
    view.set_interaction_mode(InteractionMode.DRAW)
    QTest.keyPress(view, Qt.Key_Space)
    QApplication.sendEvent(view, QFocusEvent(QEvent.FocusOut))
    assert not view._space_pressed
    assert not view._panning
    assert view.interaction_mode() == InteractionMode.DRAW


def test_mask_select_pan_cancel_and_undo(qt_app):
    widget = MaskEditor()
    widget.resize(800, 600)
    pixmap = QPixmap(1200, 900)
    pixmap.fill(Qt.darkGray)
    widget.set_background(pixmap)
    widget.show()
    qt_app.processEvents()
    view = widget._view
    original = widget.mask()
    drag(view, QPoint(100, 100), QPoint(200, 200))
    np.testing.assert_array_equal(widget.mask(), original)
    view.set_interaction_mode(InteractionMode.DRAW)
    QTest.keyPress(view, Qt.Key_Space)
    drag(view, QPoint(100, 100), QPoint(200, 200))
    QTest.keyRelease(view, Qt.Key_Space)
    np.testing.assert_array_equal(widget.mask(), original)
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
    move(view, QPoint(210, 210))
    assert np.any(widget.mask())
    QTest.keyClick(view, Qt.Key_Escape)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(210, 210))
    np.testing.assert_array_equal(widget.mask(), original)
    assert not view.can_undo()
    view.set_interaction_mode(InteractionMode.DRAW)
    drag(view, QPoint(100, 100), QPoint(200, 200))
    painted = widget.mask()
    assert np.any(painted)
    view.set_zoom(2)
    view.fit_image_to_view()
    np.testing.assert_array_equal(widget.mask(), painted)
    view.undo()
    np.testing.assert_array_equal(widget.mask(), original)
    view.redo()
    np.testing.assert_array_equal(widget.mask(), painted)
    widget.close()
    widget.deleteLater()


def test_nested_roi_editor_keeps_navigation_when_history_hidden(qt_app):
    widget = ROIEditor(show_toolbar=False)
    widget.show()
    qt_app.processEvents()
    assert widget._navigation.isVisible()
    assert not widget._btn_undo.isVisible()
    widget.close()
    widget.deleteLater()


def test_workspace_roi_mask_json_roundtrip(qt_app, tmp_path):
    import json
    from app.ui.roi_mask_editor import LocatorROIEditor
    from app.models.schema import Tool, ToolRoi, ToolMask

    roi = ToolRoi({"x": 500, "y": 300, "w": 200, "h": 100})
    mask = np.zeros((900, 1200), dtype=np.uint8)
    mask[320:350, 540:570] = 255
    tool = Tool(type="ssim", name="Existing tool", roi=roi, ignore_mask=ToolMask(mask))
    pixmap = QPixmap(1200, 900)
    pixmap.fill(Qt.black)
    editor = LocatorROIEditor()
    editor.set_background(pixmap)
    editor.set_roi_data(tool.roi.to_dict())
    editor.configure_ignore_mask(True, tool.ignore_mask.value)
    editor.show()
    qt_app.processEvents()
    editor._view.set_zoom(2)
    editor._view.set_interaction_mode(InteractionMode.PAN)
    drag(editor._view, QPoint(200, 200), QPoint(260, 250))
    editor._view.fit_image_to_view()
    tool.roi = ToolRoi.from_obj(editor.roi_data())
    tool.ignore_mask = ToolMask(editor.ignore_mask())
    path = tmp_path / "tool.json"
    path.write_text(json.dumps(tool.to_dict()))
    saved = Tool.from_dict(json.loads(path.read_text()))
    reopened = LocatorROIEditor()
    reopened.set_background(pixmap)
    reopened.set_roi_data(saved.roi.to_dict())
    reopened.configure_ignore_mask(True, saved.ignore_mask.value)
    assert reopened.roi() == (500, 300, 200, 100)
    np.testing.assert_array_equal(reopened.ignore_mask(), mask)
    assert reopened._view.interaction_mode() == InteractionMode.SELECT
    editor.close()
    reopened.close()
    editor.deleteLater()
    reopened.deleteLater()


def test_multiple_roi_edits_keep_entire_undo_redo_history(editor):
    view = editor._view
    before = editor.roi()
    first, second = (510, 310, 200, 100), (530, 320, 180, 120)
    view.edit_roi(first)
    view.edit_roi(second)
    for _ in range(2):
        view.undo()
        assert editor.roi() == first
        view.undo()
        assert editor.roi() == before
        assert not view.can_undo()
        view.redo()
        assert editor.roi() == first
        view.redo()
        assert editor.roi() == second
        assert not view.can_redo()


def test_locator_switch_resets_history_and_preserves_areas(qt_app):
    from app.ui.roi_mask_editor import LocatorROIEditor
    widget = LocatorROIEditor()
    pixmap = QPixmap(800, 600)
    pixmap.fill(Qt.darkGray)
    widget.set_background(pixmap)
    search = dict(x=10, y=10, w=400, h=300)
    template = dict(x=50, y=50, w=100, h=80)
    try:
        widget.set_locator_mode(True)  # Adding a new locator with no ROI.
        widget.set_locator_mode(True, search=search, template=template)
        assert widget._view.roi_data() == search
        widget._view.edit_roi((20, 20, 400, 300))
        assert widget._view.can_undo()
        widget._view.undo()
        assert widget._view.can_redo()
        widget.select_locator_roi('template')
        assert widget._view.roi_data() == template
        assert not widget._view.can_undo()
        assert not widget._view.can_redo()
        widget.select_locator_roi('search')
        assert widget._view.roi_data() == search
        widget.set_locator_mode(False)
    finally:
        widget.close()
        widget.deleteLater()
        qt_app.processEvents()

"""RUN geometry and station navigation without production hardware or data."""
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from app.services.camera_service import CameraService
from app.services.pico_service import PicoService
from app.services.workstation_devices import StationBinding, WorkstationDevices
from app.ui.main_window import MainWindow
from app.ui.theme import DARK_STYLE
from app.ui.workstation_window import WorkstationWindow
from test_workstation_window import FakeWorkspace


def test_real_station_shell_fits_touch_monitor(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLE)
    monkeypatch.setattr(MainWindow, '_start_production', lambda self: False)
    monkeypatch.setattr(MainWindow, '_refresh_manual_light', lambda self: None)
    WorkstationDevices(tmp_path / 'workstation_devices.json').save([
        StationBinding(f'camera_{i}', f'Kamera {i}', f'usb{i}', f'pico{i}') for i in range(1, 5)])
    window = WorkstationWindow(tmp_path)
    window.resize(1366, 768)
    window.show()
    try:
        app.processEvents()
        workspace = window.workspaces[0]
        workspace.lbl_status.setText('RUN pripravený')
        app.processEvents()
        assert window.width() == 1366
        assert window.height() == 768
        assert window.minimumSizeHint().height() <= 768
        label = workspace.lbl_status
        assert label.fontMetrics().horizontalAdvance(label.text()) <= label.contentsRect().width()
        assert label.height() >= label.fontMetrics().height()
        assert workspace.btn_trigger.height() >= 44
        assert isinstance(workspace.chk_show_roi, QPushButton)
        assert workspace.chk_show_roi.isCheckable()
        workspace.chk_show_roi.setChecked(True)
        assert workspace.chk_show_roi.text() == 'ROI zapnuté'
        workspace.chk_show_roi.setChecked(False)
        assert workspace.chk_show_roi.text() == 'ROI vypnuté'
        toolbar_position = workspace.btn_trigger.mapTo(window, QPoint())
        # Exercise the actual NOK comparison timer and dynamically wrapped
        # messages, rather than just the empty ready screen.
        workspace.mode = 'RUN'
        frame = np.zeros((480, 640), dtype=np.uint8)
        workspace._apply_run_status_style('nok')
        workspace._run_status_message.setText('NOK · Vyhľadanie a zarovnanie vzoru')
        workspace._nok_comparison.start(frame, frame)
        for _ in range(3):
            workspace._nok_comparison.advance()
            app.processEvents()
            assert window.size().width() == 1366
            assert window.size().height() == 768
            assert window.minimumSizeHint().height() <= 768
            assert workspace.btn_trigger.mapTo(window, QPoint()) == toolbar_position
        workspace._run_status_message.setText(
            'Kontrola vyžaduje overenie: dlhšie hlásenie z vyhľadania a zarovnania vzoru. ' * 12)
        workspace.recovery_notice.setText('Obnova snímania: čakám na dokončenie prípravy. ' * 8)
        workspace.recovery_notice.show()
        workspace._set_metrics_rows([(f'Nástroj {i}', 'NOK') for i in range(30)])
        app.processEvents()
        assert window.height() == 768
        assert window.minimumSizeHint().height() <= 768
        assert not workspace.side_panel.findChildren(QScrollArea)
        assert not workspace.run_details.findChildren(QScrollArea)
        for button in (workspace.btn_trigger, workspace.btn_live, workspace.btn_manual_light,
                       workspace.btn_run_options):
            assert window.rect().contains(QRect(button.mapTo(window, QPoint()), button.size()))
        workspace.btn_result_details.click()
        app.processEvents()
        assert workspace.run_details.isVisible()
        assert len(workspace.run_details._pages) > 1
        assert 'Nástroj 29' in workspace.run_details._text
        for _ in range(len(workspace.run_details._pages)-1):
            workspace.run_details.next.click()
        assert not workspace.run_details.next.isEnabled()
        assert workspace.run_details.previous.isEnabled()
        assert 'Nástroj 29' in ''.join(workspace.run_details._pages)
        workspace.run_details.close_button.click()
        for count in (4, 12):
            workspace.view_strip.set_views([
                {'id': f'view_{i}', 'name': f'View {i}'} for i in range(1, count+1)])
            workspace.view_strip.set_active('view_1')
            app.processEvents()
            assert window.height() == 768
            assert window.minimumSizeHint().height() <= 768
            assert len([b for b in workspace.view_strip._items.values() if b.isVisible()]) == 4
            for button in workspace.view_strip._items.values():
                if button.isVisible():
                    assert workspace.side_panel.rect().contains(QRect(
                        button.mapTo(workspace.side_panel, QPoint()), button.size()))
        workspace.inspection.prepare(lambda: None, lambda: None)
        window.refresh_status()
        selected = next(b for b in window.station_buttons if b.isChecked())
        assert selected.property('status') == 'nok'
        # Also cover a maximized desktop window with OS bars consuming height.
        window.resize(1366, 704)
        app.processEvents()
        assert window.height() == 704
        assert window.rect().contains(QRect(workspace.btn_trigger.mapTo(window, QPoint()),
                                            workspace.btn_trigger.size()))
        window.resize(1366, 768)
        app.processEvents()
        assert all(button.isHidden() for button in (
            workspace.btn_mode_run, workspace.mode_btn, workspace.btn_results, workspace.btn_troubleshooting))
        workspace.btn_run_options.click()
        app.processEvents()
        assert window.height() == 768
        assert workspace.live_view.rect().contains(workspace.run_options.geometry())
        window.select_station(1)
        assert not workspace.run_options.isVisible()
    finally:
        for workspace in window.workspaces:
            workspace._nok_comparison.stop()
        for workspace in window.workspaces:
            workspace._close_ready = True
        window.close()
        app.processEvents()


def test_run_overlay_does_not_move_image_or_controls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLE)
    monkeypatch.setattr(MainWindow, '_start_production', lambda self: False)
    monkeypatch.setattr(MainWindow, '_refresh_manual_light', lambda self: None)
    window = MainWindow(camera=CameraService('/dev/test-only'),
        pico=PicoService(port='/dev/test-only'), data_root=tmp_path)
    window.resize(1366, 650)
    window.show()
    app.processEvents()
    try:
        before = [widget.geometry() for widget in
                  (window.live_view, window.side_panel, window.btn_run_options)]
        QTest.mouseClick(window.btn_run_options, Qt.LeftButton)
        app.processEvents()
        assert window.run_options.isVisible()
        assert before == [widget.geometry() for widget in
                          (window.live_view, window.side_panel, window.btn_run_options)]
        assert window.live_view.rect().contains(window.run_options.geometry())
        assert window.run_options.close_button.height() >= 44
        # Interacting with options keeps them open, including a combo popup.
        QTest.mouseClick(window.chk_filtered_roi, Qt.LeftButton)
        assert window.run_options.isVisible()
        window.cmb_roi_tool.showPopup()
        app.processEvents()
        QTest.keyClick(window.cmb_roi_tool, Qt.Key_Escape)
        window.cmb_roi_tool.hidePopup()
        QTest.keyClick(window.run_options.close_button, Qt.Key_Escape)
        assert not window.run_options.isVisible()
        assert not window.btn_run_options.isChecked()
        QTest.mouseClick(window.btn_run_options, Qt.LeftButton)
        QTest.mouseClick(window.live_view, Qt.LeftButton, pos=window.live_view.rect().topLeft())
        assert not window.run_options.isVisible()
        QTest.mouseClick(window.btn_run_options, Qt.LeftButton)
        window.hide()
        assert not window.run_options.isVisible()
    finally:
        window._close_ready = True
        window.close()
        app.processEvents()


def test_many_stations_overview_and_selection_preserve_runtime(tmp_path):
    app = QApplication.instance() or QApplication([])
    WorkstationDevices(tmp_path / 'workstation_devices.json').save([
        StationBinding(f'camera_{i}', f'Kamera {i}', f'usb{i}', f'pico{i}')
        for i in range(1, 13)])
    window = WorkstationWindow(tmp_path, workspace_factory=FakeWorkspace)
    window.resize(1366, 768)
    window.show()
    app.processEvents()
    try:
        states = [w.inspection.snapshot() for w in window.workspaces]
        window.overview_button.click()
        assert window.pages.currentWidget() is window.overview
        assert len(window.overview_cards) == 12
        window.overview_cards[10].click()
        assert window.tabs.currentIndex() == 10
        assert window.pages.currentWidget() is window.tabs
        assert any(b.property('stationIndex') == 10 and b.isChecked()
                   for b in window.station_buttons if b.isVisible())
        assert [w.inspection.snapshot() for w in window.workspaces] == states
        for button in window.station_buttons:
            if button.isVisible():
                assert window.centralWidget().rect().contains(button.geometry())
        window.resize(900, 650)
        app.processEvents()
        assert sum(b.isVisible() for b in window.station_buttons) <= 2
        assert [w.inspection.snapshot() for w in window.workspaces] == states
    finally:
        window.close()
        app.processEvents()
        app.processEvents()


def test_reconfigured_stations_replace_navigation(tmp_path):
    app = QApplication.instance() or QApplication([])
    bindings = [StationBinding(f'camera_{i}', f'Kamera {i}', f'usb{i}', f'pico{i}')
                for i in range(1, 4)]
    WorkstationDevices(tmp_path / 'workstation_devices.json').save(bindings)
    window = WorkstationWindow(tmp_path, workspace_factory=FakeWorkspace)
    window.show()
    app.processEvents()
    window._reconfigure = bindings[:2]
    for workspace in window.workspaces:
        workspace.close()
    app.processEvents()
    app.processEvents()
    assert len(window.workspaces) == window.tabs.count() == len(window.overview_cards) == 2
    assert len(window.station_buttons) <= 2
    window.close()
    app.processEvents()
    app.processEvents()

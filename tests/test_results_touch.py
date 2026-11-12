import csv
import json
import sqlite3
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDate, Qt, QPoint
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QDateEdit, QTableView, QPushButton

from app.services.db_service import SCHEMA
from app.services.results_history import query_history, history_cursor, export_history, history_options
from app.ui.results_page import ResultsPage
from app.ui.theme import DARK_STYLE


@pytest.fixture
def sources(tmp_path):
    image = QImage(640, 400, QImage.Format_RGB32)
    image.fill(QColor('#8b9095'))
    image.save(str(tmp_path / 'capture.png'))
    sources = []
    stamp = int(datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()*1000)
    for number in (1, 2):
        path = tmp_path / f'camera{number}.db'
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.execute('INSERT INTO recipes(name) VALUES (?)', (f'Recept {number}',))
        for index in range(18):
            meta = dict(view_name=f'Pohľad {index%4+1}', cycle_time_ms=101.0268,
                per_tool=[dict(name=name, status='nok' if index%3 == 0 else 'ok',
                    metrics={'score': 0.97, 'area': 236}, thresholds={'max_area': 100},
                    history_overlays=[dict(rect=[20, 30, 80, 50], error=True)])
                    for name in ('Vyhľadanie a zarovnanie vzoru', 'Kontrola prázdnej formy', 'SSIM', 'Štvrtý nástroj')])
            # Repeated timestamps and IDs in different station databases.
            db.execute('INSERT INTO results(ts_ms,recipe_id,ok,view_id,full_path,meta_json) VALUES (?,1,?,?,?,?)',
                (stamp + (index//2)*1000, index%3 != 0, f'view_{index%4+1}', str(tmp_path/'capture.png'), json.dumps(meta)))
        db.commit(); db.close()
        sources.append(dict(id=f'camera_{number}', name=f'Kamera {number}', db_path=path, active=number==1))
    return sources


def test_station_history_keyset_paging_filters_and_export(sources, tmp_path):
    filters = dict(start=0, end=9999999999999, recipe='', view='', ok=None)
    all_rows = query_history(sources, filters, limit=100)
    assert len(all_rows) == 36
    rows, cursor = [], None
    while True:
        page = query_history(sources, filters, cursor, 5)
        if not page:
            break
        rows += page
        cursor = history_cursor(page[-1])
    assert [history_cursor(r) for r in rows] == [history_cursor(r) for r in all_rows]
    assert len({history_cursor(r) for r in rows}) == 36
    filtered = query_history(sources, dict(filters, recipe='Recept 2', ok=0), limit=100)
    assert filtered and all(r['station_id']=='camera_2' and not r['ok'] for r in filtered)
    assert query_history(sources, dict(filters, recipe="' OR 1=1 --")) == []
    assert history_options(sources) == (['Recept 1', 'Recept 2'], ['view_1','view_2','view_3','view_4'])
    output = tmp_path/'export.csv'
    assert export_history(sources, filters, output) == 36
    with output.open(encoding='utf-8-sig') as stream:
        exported=list(csv.reader(stream, delimiter=';'))
    assert len(exported) == 37 and exported[0][0]=='Kamera'
    assert {row[0] for row in exported[1:]} == {'Kamera 1', 'Kamera 2'}
    with pytest.raises(FileExistsError):
        export_history(sources, filters, output)


def pump(app, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def page(sources):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLE)
    shell = QWidget()
    layout = QVBoxLayout(shell)
    chrome = QLabel('HDF VISION · VÝSLEDKY\nKamera 1 · Recept 1')
    chrome.setFixedHeight(180)
    layout.addWidget(chrome)
    page = ResultsPage(sources[0]['db_path'], sources=lambda:sources)
    layout.addWidget(page,1)
    shell.setFixedSize(1366,768); shell.show()
    page.activate()
    pump(app, lambda:page._has_image and not page.jobs and not page._resize_timer.isActive())
    yield SimpleNamespace(page=page, app=app, shell=shell)
    page.pool.waitForDone()
    app.processEvents()
    shell.close(); shell.deleteLater(); app.processEvents()


def test_touch_history_fits_and_selection_details_and_navigation(page):
    p, app = page.page, page.app
    assert len(p.rows) == p._page_size and 4 <= p._page_size <= 8
    assert p.canvas.width() > 800 and p.canvas.height() > 200
    assert page.shell.height() == 768
    assert p.geometry().bottom() < 768
    assert all(card.height() >= 48 for card in p.cards if card.isVisible())
    assert p.cycle_time.text() == 'Kontrola: 101 ms'
    assert p.cards[0].isChecked()
    assert all(card.geometry().bottom() < p.previous.parentWidget().height() for card in p.cards if card.isVisible())
    assert not any('False' in b.text() for b in p.findChildren(QPushButton))
    p.cards[1].click()
    pump(app, lambda: not p.jobs)
    assert p._selected_row == 1 and p.cards[1].isChecked()
    assert p.image_title.text().startswith('Kamera 1')
    before = p.canvas.geometry()
    p._open_details(); app.processEvents()
    assert p.detail_overlay.isVisible() and p.details.rowCount() == 12
    assert p.canvas.geometry() == before
    p.detail_overlay.hide()
    p._next_tools(); p.tool_buttons[0].click(); app.processEvents()
    assert p.tool.currentIndex() == 4 and p.details.rowCount() == 3
    p.detail_overlay.hide()
    p.errors.click(); assert not p.overlays
    p.roi.click(); p.errors.click(); assert p.overlays
    first = history_cursor(p.rows[0]); p.next.click()
    pump(app,lambda:not p.jobs)
    assert len(p.cursors)==2 and history_cursor(p.rows[0]) < first
    p.previous.click(); pump(app,lambda:not p.jobs)
    assert history_cursor(p.rows[0]) == first
    page.shell.grab().save('/testdeps/results-touch-actual.png')


def test_date_filter_calendar_manual_entry_cancel_and_multicamera(page):
    p, app = page.page, page.app
    original = p.start.date()
    before = p.canvas.geometry()
    p._open_filters(); app.processEvents()
    assert not p.start.hasFocus()
    assert p.canvas.geometry() == before
    assert p.start.calendarPopup() and p.end.calendarPopup()
    p.start.setDate(original.addDays(-5))
    p.filter_overlay.hide()
    assert p.start.date() == original
    p._open_filters()
    # Keyboard entry through the editable QDateEdit line edit.
    p.start.setFocus()
    p.start.setSelectedSection(QDateEdit.DaySection)
    QTest.keyClicks(p.start, '01')
    QTest.keyClick(p.start, Qt.Key_Tab)
    assert p.start.date() == QDate(original.year(), original.month(), 1)
    QTest.mouseClick(p.end, Qt.LeftButton, pos=QPoint(p.end.width()-20, p.end.height()//2))
    app.processEvents()
    calendar = p.end.calendarWidget()
    assert calendar.isVisible()
    grid = calendar.findChild(QTableView)
    target = None
    for row in range(1, grid.model().rowCount()):
        for column in range(grid.model().columnCount()):
            index = grid.model().index(row, column)
            if str(index.data()) == '5' and not grid.isColumnHidden(column):
                target = index
                break
        if target is not None:
            break
    assert target is not None
    QTest.mouseClick(grid.viewport(), Qt.LeftButton, pos=grid.visualRect(target).center())
    app.processEvents()
    assert p.end.date().day() == 5
    assert not calendar.isVisible()
    p.end.setDate(QDate.currentDate())
    p._apply_filters()
    pump(app, lambda:not p.jobs)
    assert not p.filter_overlay.isVisible()
    p._open_filters(); p.start.setDate(p.end.date().addDays(1)); p._apply_filters()
    assert p.filter_error.isVisible() and p.filter_overlay.isVisible()
    p.filter_overlay.hide()
    p.camera.setCurrentIndex(p.camera.findData(''))
    pump(app, lambda:not p.jobs)
    assert {row['station_id'] for row in p.rows} == {'camera_1','camera_2'}
    p._status_changed(0); pump(app,lambda:not p.jobs)
    assert p.rows and all(not row['ok'] for row in p.rows)
    p._open_filters(); app.processEvents()
    page.shell.grab().save('/testdeps/results-filters-actual.png')
    calendar=p.start.calendarWidget(); calendar.show(); app.processEvents()
    calendar.grab().save('/testdeps/results-calendar-actual.png')
    calendar.hide()


def test_results_in_real_station_shell(sources, tmp_path, monkeypatch):
    import shutil
    from app.ui.main_window import MainWindow
    from app.ui.workstation_window import WorkstationWindow
    from app.services.workstation_devices import WorkstationDevices, StationBinding
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLE)
    data = tmp_path/'workstation'
    data.mkdir()
    WorkstationDevices(data/'workstation_devices.json').save([
        StationBinding(f'camera_{i}',f'Kamera {i}',f'camera-serial-{i}',f'pico-serial-{i}') for i in (1,2)])
    for i,source in enumerate(sources,1):
        folder=data if i==1 else data/'stations'/f'camera_{i}'
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(source['db_path'],folder/'HDF_Vision.db')
    monkeypatch.setattr(MainWindow,'_start_production',lambda self:False)
    monkeypatch.setattr(MainWindow,'_refresh_manual_light',lambda self:None)
    window=WorkstationWindow(data)
    window.setFixedSize(1366,768); window.show()
    try:
        window._navigate('RESULTS')
        p=window.workspaces[0].panel_results
        pump(app,lambda:p._has_image and not p.jobs and not p._resize_timer.isActive())
        window.grab().save('/testdeps/results-real-shell.png')
        assert p.camera.count()==3
        assert p.canvas.height()>280 and p.canvas.width()>800
        assert p.mapTo(window,p.rect().bottomRight()).y()<768
        window.grab().save('/testdeps/results-real-shell.png')
        p._open_filters(); app.processEvents()
        window.grab().save('/testdeps/results-real-shell-filters.png')
        window.workspaces[0].mode='RUN'
        window._navigate('RUN'); app.processEvents()
        assert window.minimumSizeHint().height() <= 768
        for height in (768, 704):
            window.showNormal()
            window.setFixedSize(1366, height)
            window.showFullScreen(); app.processEvents()
            assert window.isFullScreen()
            for _ in range(2):
                window._navigate('RESULTS')
                pump(app,lambda:not p.jobs and not p._resize_timer.isActive())
                assert window.isFullScreen()
                window._navigate('RUN'); app.processEvents()
                assert window.isFullScreen()
                assert window.minimumSizeHint().height() <= height
    finally:
        for workspace in window.workspaces:
            workspace._close_ready=True
            if workspace.panel_results is not None:
                workspace.panel_results.pool.waitForDone()
        window.close(); app.processEvents()


def test_saved_nok_context_and_ssim_threshold_render_without_roi_toggle(page):
    from copy import deepcopy
    from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsTextItem
    from app.services.results_overlays import result_overlays
    p, app = page.page, page.app
    report = dict(name='Podobnosť vzhľadu (SSIM)', type='ssim', status='nok',
        metrics={'ssim': .97}, thresholds={'ssim_min': .99},
        history_overlays=[dict(rect=[20,30,180,110], error=False)])
    saved = deepcopy(report)
    p.reports = [report]; p.tool.clear(); p.tool.addItem('Všetky nástroje', -1)
    p.roi.setChecked(False); p.errors.setChecked(True); p._draw()
    rectangles = [item for item in p.overlays if isinstance(item,QGraphicsRectItem)]
    labels = [item for item in p.overlays if isinstance(item,QGraphicsTextItem)]
    assert len(rectangles) == 1 and rectangles[0].rect().getRect() == (20,30,180,110)
    assert rectangles[0].pen().color().name() == '#ff5353'
    assert labels and '97% < 99%' in labels[0].toPlainText()
    assert 'Kontrolovaná ROI' in labels[0].toPlainText()
    assert report == saved
    page.shell.grab().save('/testdeps/results-ssim-nok-fixed.png')
    p.errors.setChecked(False); assert not p.overlays
    p.roi.setChecked(True)
    assert all(item.pen().color().name()=='#49aaff' for item in p.overlays)
    p.roi.setChecked(False); p.errors.setChecked(True)
    fault = dict(report, type='mold.protection_v1', name='Kontrola prázdnej formy',
                 metrics={'decision_reason':'model_not_ready'})
    assert 'Model nie je pripraveny' in result_overlays(fault)[-1]['label']
    precise = dict(report, history_overlays=[dict(rect=[20,30,180,110],error=False),
                                           dict(rect=[50,60,10,15],error=True)])
    assert len([item for item in result_overlays(precise) if item.get('error')]) == 1
    assert not any(item.get('context') for item in result_overlays(precise))
    assert not any(item.get('error') for item in result_overlays(dict(report,status='ok')))


@pytest.mark.parametrize('roi', [dict(x=10,y=20,w=30,h=40),
    dict(shape='polygon', points=[[10,20],[40,25],[35,60],[5,55]],rotated_rect=True),
    dict(shape='ellipse', x=10,y=20,w=30,h=40)])
def test_legacy_saved_roi_supports_nok_context_without_current_recipe(roi):
    from app.services.results_overlays import result_overlays
    report=dict(name='SSIM',type='ssim',status='nok',roi=roi,metrics={'ssim':.5},thresholds={'ssim_min':.9})
    entries=result_overlays(report)
    assert len(entries)==1 and entries[0]['error'] and entries[0]['context']
    if roi.get('shape') == 'polygon':
        assert [list(point) for point in entries[0]['points']] == roi['points']
    if roi.get('shape') == 'ellipse':
        assert len(entries[0]['points']) == 64

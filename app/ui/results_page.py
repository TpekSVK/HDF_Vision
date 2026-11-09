"""Operator history browser. Workers return data/QImage, never QPixmap/widgets."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QDate, QPointF, QTimer, QLocale
from PySide6.QtGui import QImageReader, QPixmap, QColor, QPen, QPolygonF, QPainterPath, QTextCharFormat, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QFileDialog, QFrame, QGraphicsItem, QGridLayout, QButtonGroup, QSizePolicy, QToolButton,
)

from app.services.results_browser import metadata
from app.services.results_overlays import result_overlays
from app.services.results_history import query_history, history_options, history_cursor, export_history
from app.ui.image_canvas import ImageView, ImageNavigationToolbar, InteractionMode
from app.ui.results_widgets import HistoryCard, ResultsOverlay, HistoryDateEdit


class _Signals(QObject):
    done = Signal(str, int, object, str)


class _Job(QRunnable):
    def __init__(self, kind, token, function):
        super().__init__()
        self.kind, self.token, self.function = kind, token, function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.kind, self.token, self.function(), '')
        except Exception as exc:
            self.signals.done.emit(self.kind, self.token, None, str(exc))


def _image(row):
    for key in ('full_path', 'thumb_path'):
        path = row.get(key)
        if path and Path(path).is_file():
            reader = QImageReader(str(path))
            image = reader.read()
            if not image.isNull():
                return image, key == 'thumb_path'
    return None, False


class ResultsPage(QWidget):
    PAGE_SIZE = 8

    def set_production_active(self, active: bool) -> None:
        self._production_active = active
        self.production_notice.setText('● Kontroly sú aktívne' if active else 'Kontroly sú pozastavené')
        self.production_notice.setToolTip(
            'Externé vstupy sa spracúvajú aj počas prehliadania výsledkov.' if active
            else 'Kontroly obnovíte prechodom do RUN.')

    def __init__(self, db_path, parent=None, *, sources=None, station_id='camera_1', station_name='Kamera 1'):
        super().__init__(parent)
        self.setObjectName('resultsPage')
        # A hidden history page must not force its card stack's minimum height
        # onto RUN when the station header is shown again.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.db_path = db_path
        self.station_id = station_id
        self._source_provider = sources
        self._local_source = dict(id=station_id, name=station_name, db_path=db_path)
        self._sources = [self._local_source]
        self._production_active = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.jobs, self.tokens = {}, {}
        self._pending_image = None
        self.rows, self.cursors = [], [None]
        self.meta, self.overlays, self.reports = {}, [], []
        self._selected_row = -1
        self._has_image = False
        self._page_size = 1
        self._filter_snapshot = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._adapt_page_size)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        title = QLabel('Výsledky'); title.setProperty('role', 'appTitle')
        toolbar.addWidget(title)
        self.camera = QComboBox()
        self.camera.currentIndexChanged.connect(self._camera_changed)
        toolbar.addWidget(self.camera)
        toolbar.addStretch(1)
        self.status = QComboBox(self)  # Value model shared by filters and export.
        self.status.hide()
        self.status_buttons = QButtonGroup(self)
        for label, value in [('Všetky', None), ('NOK', 0), ('OK', 1)]:
            self.status.addItem(label, value)
            button = QPushButton(label); button.setCheckable(True)
            button.setChecked(value is None)
            self.status_buttons.addButton(button)
            button.clicked.connect(lambda checked=False, v=value: self._status_changed(v))
            toolbar.addWidget(button)
        self.filter_button = QPushButton('Filtre')
        self.filter_button.clicked.connect(self._open_filters)
        toolbar.addWidget(self.filter_button)
        self.export = QPushButton('Export CSV')
        self.export.clicked.connect(self._export)
        toolbar.addWidget(self.export)
        layout.addLayout(toolbar)
        context = QHBoxLayout()
        self.filter_summary = QLabel()
        self.filter_summary.setProperty('role', 'secondary')
        self.filter_summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        context.addWidget(self.filter_summary, 1)
        self.production_notice = QLabel()
        self.production_notice.setProperty('role', 'secondary')
        self.set_production_active(False)
        context.addWidget(self.production_notice)
        layout.addLayout(context)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.history_panel = QFrame(); self.history_panel.setProperty('role', 'panel')
        self.history_panel.setFixedWidth(330)
        history_layout = QVBoxLayout(self.history_panel)
        history_layout.setContentsMargins(8, 8, 8, 8)
        history_layout.setSpacing(5)
        history_layout.addWidget(QLabel('História kontrol'))
        self.cards = []
        self.history_group = QButtonGroup(self)
        for index in range(self.PAGE_SIZE):
            card = HistoryCard()
            self.history_group.addButton(card)
            card.clicked.connect(lambda checked=False, i=index: self._choose_row(i))
            history_layout.addWidget(card)
            card.hide()
            self.cards.append(card)
        self.empty_history = QLabel('Žiadne kontroly pre zvolené filtre.')
        self.empty_history.setWordWrap(True)
        history_layout.addWidget(self.empty_history)
        history_layout.addStretch(1)
        paging = QHBoxLayout()
        self.previous, self.next = QPushButton('‹'), QPushButton('›')
        self.previous.setAccessibleName('Predchádzajúca strana')
        self.next.setAccessibleName('Ďalšia strana')
        self.previous.clicked.connect(self._previous)
        self.next.clicked.connect(self._next)
        self.page_label = QLabel('Strana 1'); self.page_label.setAlignment(Qt.AlignCenter)
        paging.addWidget(self.previous); paging.addWidget(self.page_label, 1); paging.addWidget(self.next)
        history_layout.addLayout(paging)
        body.addWidget(self.history_panel)
        self.viewer = QFrame(); self.viewer.setProperty('role', 'panel')
        viewer_layout = QVBoxLayout(self.viewer)
        viewer_layout.setContentsMargins(8, 8, 8, 8)
        viewer_layout.setSpacing(6)
        self.canvas = ImageView()
        self.canvas.setBackgroundBrush(QColor('#11161b'))
        self.canvas.set_interaction_mode(InteractionMode.PAN)
        self.navigation = ImageNavigationToolbar(self.canvas)
        for button in self.navigation.mode_buttons.values():
            button.hide()
        self.navigation.actual_size_button.hide()
        self.roi, self.errors = QPushButton('ROI'), QPushButton('Chyby')
        self.errors.setChecked(False)
        for button in (self.roi, self.errors):
            button.setCheckable(True)
            self.navigation._context_layout.addWidget(button)
        self.errors.setChecked(True)
        viewer_layout.addWidget(self.navigation)
        self.image_title = QLabel('Vyberte kontrolu zo zoznamu.')
        self.image_title.setProperty('role', 'secondary')
        viewer_layout.addWidget(self.image_title)
        viewer_layout.addWidget(self.canvas, 1)
        outcome = QHBoxLayout()
        self.result = QLabel('VÝSLEDOK KONTROLY —')
        self.result.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        outcome.addWidget(self.result, 1)
        self.cycle_time = QLabel('')
        outcome.addWidget(self.cycle_time)
        self.details_button = QPushButton('Podrobnosti')
        self.details_button.clicked.connect(self._open_details)
        outcome.addWidget(self.details_button)
        viewer_layout.addLayout(outcome)
        tools_row = QHBoxLayout()
        self.tool_buttons = []
        for offset in range(3):
            button = QPushButton()
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(lambda checked=False, i=offset: self._open_tool_details(i))
            tools_row.addWidget(button, 1)
            self.tool_buttons.append(button)
        self.tool_page = 0
        self.tools_next = QPushButton('›')
        self.tools_next.setAccessibleName('Ďalšie nástroje')
        self.tools_next.clicked.connect(self._next_tools)
        tools_row.addWidget(self.tools_next)
        viewer_layout.addLayout(tools_row)
        self.image_note = QLabel('')
        self.image_note.setWordWrap(True)
        self.image_note.setProperty('role', 'secondary')
        body.addWidget(self.viewer, 1)
        layout.addLayout(body, 1)
        self.message = QLabel('Vyberte kontrolu zo zoznamu.')
        self.message.setProperty('role', 'secondary')
        self.message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.message)
        self.previous.setEnabled(False); self.next.setEnabled(False)
        self._build_filters()
        self._build_details()
        self.roi.toggled.connect(self._draw)
        self.errors.toggled.connect(self._draw)
        self._update_sources()
        self._update_filter_summary()
        self._update_tool_buttons()

    @staticmethod
    def _view_name(value):
        import re
        text = str(value or '—')
        return re.sub(r'^view[_ -](\d+)$', r'Pohľad \1', text, flags=re.I)

    def _build_filters(self):
        self.filter_overlay = ResultsOverlay(self, 'Filtre výsledkov')
        grid = QGridLayout()
        self.start = HistoryDateEdit(QDate.currentDate().addDays(-7))
        self.end = HistoryDateEdit(QDate.currentDate())
        for column, (label, control) in enumerate([('Od', self.start), ('Do', self.end)]):
            control.setLocale(QLocale('sk_SK'))
            control.setCalendarPopup(True)
            control.setDisplayFormat('dd.MM.yyyy')
            control.setKeyboardTracking(False)
            calendar = control.calendarWidget()
            calendar.setVerticalHeaderFormat(calendar.VerticalHeaderFormat.NoVerticalHeader)
            calendar.setMinimumSize(420, 366)
            calendar.setLocale(QLocale('sk_SK'))
            calendar.setFirstDayOfWeek(Qt.Monday)
            weekend = QTextCharFormat()
            weekend.setForeground(QColor('#e6e8eb'))
            calendar.setWeekdayTextFormat(Qt.Saturday, weekend)
            calendar.setWeekdayTextFormat(Qt.Sunday, weekend)
            for name, text in [('qt_calendar_prevmonth', '‹'), ('qt_calendar_nextmonth', '›')]:
                arrow = calendar.findChild(QToolButton, name)
                if arrow is not None:
                    arrow.setIcon(QIcon()); arrow.setText(text)
            grid.addWidget(QLabel(label), 0, column)
            grid.addWidget(control, 1, column)
        self.recipe, self.view = QComboBox(), QComboBox()
        self.recipe.addItem('Všetky recepty', '')
        self.view.addItem('Všetky pohľady', '')
        for column, (label, control) in enumerate([('Recept', self.recipe), ('Pohľad', self.view)]):
            grid.addWidget(QLabel(label), 2, column)
            grid.addWidget(control, 3, column)
        self.filter_overlay.controls.addLayout(grid)
        self.filter_error = QLabel(); self.filter_error.setWordWrap(True)
        self.filter_error.setStyleSheet('color: #ff8585;')
        self.filter_error.hide()
        self.filter_overlay.controls.addWidget(self.filter_error)
        actions = QHBoxLayout(); actions.addStretch(1)
        cancel = QPushButton('Zrušiť'); cancel.clicked.connect(self.filter_overlay.hide)
        apply = QPushButton('Použiť filtre'); apply.setProperty('role', 'primary')
        apply.clicked.connect(self._apply_filters)
        actions.addWidget(cancel); actions.addWidget(apply)
        self.filter_overlay.controls.addLayout(actions)
        self.filter_overlay.closed.connect(self._cancel_filters)

    def _build_details(self):
        self.detail_overlay = ResultsOverlay(self, 'Podrobnosti kontroly')
        self.detail_overlay.controls.addWidget(self.image_note)
        self.tool = QComboBox()
        self.tool.currentIndexChanged.connect(self._draw)
        self.detail_overlay.controls.addWidget(self.tool)
        self.summary = QLabel(''); self.summary.setWordWrap(True)
        self.detail_overlay.controls.addWidget(self.summary)
        self.details = QTableWidget(0, 3)
        self.details.setHorizontalHeaderLabels(['Nástroj', 'Parameter', 'Hodnota'])
        self.details.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.details.verticalHeader().hide()
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.details.setMinimumHeight(220)
        self.details.setWordWrap(True)
        self.detail_overlay.controls.addWidget(self.details)

    def _update_sources(self):
        selected = self.camera.currentData() if self.camera.count() else self.station_id
        self._sources = list(self._source_provider() if self._source_provider else [self._local_source])
        self.camera.blockSignals(True)
        self.camera.clear()
        for source in self._sources:
            self.camera.addItem(source['name'], source['id'])
        if len(self._sources) > 1:
            self.camera.addItem('Všetky kamery', '')
        self.camera.setCurrentIndex(max(0, self.camera.findData(selected)))
        self.camera.blockSignals(False)
        selected_sources = self._selected_sources()
        active = [source.get('active', self._production_active) for source in selected_sources]
        if active and any(active) and not all(active):
            self.production_notice.setText('● Časť kamier je aktívna')
            self.production_notice.setToolTip('Stav jednotlivých kamier je dostupný v prehľade staníc.')
        else:
            self.set_production_active(bool(active) and all(active))

    def _selected_sources(self):
        selected = self.camera.currentData()
        return [dict(source) for source in self._sources if not selected or source['id'] == selected]

    def _camera_changed(self):
        if hasattr(self, 'start'):
            self.activate()

    def _status_changed(self, value):
        self.status.setCurrentIndex(self.status.findData(value))
        self.reload()

    def _update_filter_summary(self):
        text = f'{self.start.text()} – {self.end.text()} · {self.recipe.currentText()} · {self.view.currentText()}'
        self.filter_summary.setText(text)
        self.filter_summary.setToolTip(text)

    def _open_filters(self):
        self.detail_overlay.hide()
        self._filter_snapshot = (self.start.date(), self.end.date(), self.recipe.currentData(), self.view.currentData())
        self.filter_error.hide()
        self.filter_overlay.open(self.filter_button)

    def _cancel_filters(self):
        if self._filter_snapshot is not None:
            start, end, recipe, view = self._filter_snapshot
            self.start.setDate(start); self.end.setDate(end)
            self.recipe.setCurrentIndex(max(0, self.recipe.findData(recipe)))
            self.view.setCurrentIndex(max(0, self.view.findData(view)))
            self._filter_snapshot = None

    def _apply_filters(self):
        try:
            self._filters()
        except ValueError as exc:
            self.filter_error.setText(str(exc)); self.filter_error.show()
            self.filter_overlay._place()
            return
        self._filter_snapshot = None
        self.filter_overlay.hide()
        self._update_filter_summary()
        self.reload()

    def _open_details(self):
        self.filter_overlay.hide()
        self.detail_overlay.open(self.details_button)

    def _open_tool_details(self, offset):
        index = self.tool_page * 3 + offset
        self.tool.setCurrentIndex(index + 1)
        self._open_details()

    def _next_tools(self):
        self.tool_page = (self.tool_page + 1) % max(1, (len(self.reports) + 2) // 3)
        self._update_tool_buttons()

    def _update_tool_buttons(self):
        for offset, button in enumerate(self.tool_buttons):
            index = self.tool_page * 3 + offset
            button.setVisible(index < len(self.reports))
            if index < len(self.reports):
                report = self.reports[index]
                text = f'{report.get("name", "Nástroj")} · {str(report.get("status") or "—").upper()}'
                button.setText(text); button.setToolTip(text)
                button.setAccessibleName(text)
                status = str(report.get('status') or '').lower()
                button.setStyleSheet('color: #ff8585;' if status == 'nok' else 'color: #5cdb8c;' if status == 'ok' else '')
        self.tools_next.setVisible(len(self.reports) > 3)
        self.details_button.setEnabled(self._selected_row >= 0)

    def _choose_row(self, row):
        self._selected_row = row
        self.tool_page = 0
        self._select()
        self._update_tool_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(80)

    def hideEvent(self, event):
        self.filter_overlay.hide(); self.detail_overlay.hide()
        super().hideEvent(event)

    def _adapt_page_size(self):
        available = self.history_panel.height() - 100
        size = max(1, min(self.PAGE_SIZE, available // 59))
        if size != self._page_size:
            self._page_size = size
            if hasattr(self, 'filters'):
                self.reload()

    def _submit(self, kind, function):
        if kind == 'image' and any(key[0] == 'image' for key in self.jobs):
            # Keep only the newest selection instead of queuing full image
            # decodes while the operator scrolls through the history.
            self._pending_image = function
            return
        token = self.tokens.get(kind, 0) + 1
        self.tokens[kind] = token
        job = _Job(kind, token, function)
        self.jobs[kind, token] = job
        job.signals.done.connect(self._done)
        self.pool.start(job)

    def activate(self):
        self._update_sources()
        sources = self._selected_sources()
        self._submit('options', lambda: history_options(sources))
        self.reload()

    def _filters(self):
        start, end = self.start.date().toPython(), self.end.date().addDays(1).toPython()
        if start >= end:
            raise ValueError('Dátum Od musí byť najneskôr dátum Do.')
        return dict(start=int(datetime.combine(start, datetime.min.time()).timestamp()*1000),
                    end=int(datetime.combine(end, datetime.min.time()).timestamp()*1000),
                    recipe=self.recipe.currentData(), view=self.view.currentData(), ok=self.status.currentData())

    def reload(self):
        try:
            self.filters = self._filters()
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.cursors = [None]
        self._update_filter_summary()
        self._load()

    def _load(self):
        filters, cursor = dict(self.filters), self.cursors[-1]
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.message.setText('Načítavam výsledky…')
        sources, limit = self._selected_sources(), self._page_size + 1
        self._submit('rows', lambda: query_history(sources, filters, cursor, limit))

    def _previous(self):
        if len(self.cursors) > 1:
            self.cursors.pop()
            self._load()

    def _next(self):
        if self.rows:
            self.cursors.append(history_cursor(self.rows[-1]))
            self._load()

    def _select(self):
        self._pending_image = None
        self.tokens['image'] = self.tokens.get('image', 0) + 1
        self.overlays = []
        self.canvas.set_pixmap(None)
        self._has_image = False
        self.meta = {}
        self.reports = []
        self.tool.clear()
        self.summary.clear()
        self.details.setRowCount(0)
        self.result.setText('VÝSLEDOK KONTROLY —')
        self.cycle_time.clear()
        self.image_title.setText('Vyberte kontrolu zo zoznamu.')
        self.image_note.setText('Vyberte kontrolu zo zoznamu.')
        row = self._selected_row
        if not 0 <= row < len(self.rows):
            return
        record = dict(self.rows[row])
        self.meta = metadata(record)
        self.tool.addItem('Všetky nástroje', -1)
        reports = self.meta.get('per_tool', [])
        if not isinstance(reports, list):
            reports = []
        self.reports = [item for item in reports if isinstance(item, dict)]
        for index, item in enumerate(self.reports):
            self.tool.addItem(f"{item.get('name', 'Nástroj')} · {str(item.get('status') or '—').upper()}", index)
        view_name = self._view_name(self.meta.get('view_name') or record.get('view_id'))
        self.result.setText(('OK' if record['ok'] else 'NOK') + ' · ' + view_name)
        cycle = self.meta.get('cycle_time_ms')
        try:
            cycle_text = f'{float(cycle):.0f} ms'
        except (TypeError, ValueError):
            cycle_text = '—'
        self.cycle_time.setText(f'Kontrola: {cycle_text}')
        stamp = datetime.fromtimestamp(record['ts_ms']/1000).strftime('%d.%m.%Y %H:%M:%S')
        self.image_title.setText(f'{record["station_name"]} · {view_name} · {stamp}')
        self.result.setStyleSheet('color: #24c76a;' if record['ok'] else 'color: #ff5353;')
        self.image_note.setText('Načítavam snímku…')
        self._submit('image', lambda: _image(record))
        self._draw()

    def _draw(self):
        for item in self.overlays:
            self.canvas.scene().removeItem(item)
        self.overlays = []
        reports = getattr(self, 'reports', [])
        index = self.tool.currentData()
        selected = reports if index in (None, -1) else reports[index:index+1]
        self.summary.setText('\n'.join(f"{r.get('name', 'Nástroj')}: {str(r.get('status') or '—').upper()}" for r in selected))
        values = []
        for report in selected:
            for section, title in [('metrics', 'Metrika'), ('thresholds', 'Prah')]:
                entries = report.get(section, {})
                if isinstance(entries, dict):
                    values.extend((report.get('name', 'Nástroj'), f'{title}: {key}',
                                   json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
                                  for key, value in entries.items())
        self.details.setRowCount(len(values))
        for row, fields in enumerate(values):
            for column, value in enumerate(fields):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.details.setItem(row, column, item)
        if not self._has_image:
            return
        for report in selected:
            for entry in result_overlays(report):
                is_error = entry.get('error', False)
                if not (self.errors.isChecked() if is_error else self.roi.isChecked()):
                    continue
                pen = QPen(QColor('#ff5353' if is_error else '#49aaff'), 2)
                pen.setCosmetic(True)
                points, rect = entry.get('points'), entry.get('rect')
                if points:
                    polygon = QPolygonF([QPointF(float(x), float(y)) for x, y in points])
                    if entry.get('closed', True):
                        item = self.canvas.scene().addPolygon(polygon, pen)
                    else:
                        path = QPainterPath(polygon[0])
                        for point in list(polygon)[1:]:
                            path.lineTo(point)
                        item = self.canvas.scene().addPath(path, pen)
                elif rect and len(rect) == 4:
                    item = self.canvas.scene().addRect(*rect, pen)
                else:
                    continue
                item.setZValue(20 if is_error else 10)
                self.overlays.append(item)
                if is_error:
                    from html import escape
                    label = self.canvas.scene().addText('')
                    label.setHtml('<span style="background-color:#712b2b;color:#ffffff">' +
                                  escape(entry.get('label') or 'NOK · ' + str(report.get('name') or 'Nástroj')).replace('\n', '<br>') + '</span>')
                    label.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                    label.setTextWidth(300)
                    label.setPos(item.boundingRect().topLeft())
                    label.setZValue(30)
                    self.overlays.append(label)

    def _export(self):
        try:
            filters = dict(self.filters) if hasattr(self, 'filters') else self._filters()
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export filtrovaných výsledkov', 'vysledky.csv', 'CSV (*.csv)')
        if not path:
            return
        if Path(path).exists():
            self.message.setText('Vyberte nový názov súboru; existujúci export zostane zachovaný.')
            return
        self.export.setEnabled(False)
        self.message.setText('Exportujem všetky výsledky zodpovedajúce filtrom…')
        sources = self._selected_sources()
        self._submit('export', lambda: export_history(sources, filters, path))

    def _done(self, kind, token, data, error):
        self.jobs.pop((kind, token), None)
        if kind == 'image' and self._pending_image is not None:
            pending, self._pending_image = self._pending_image, None
            self._submit('image', pending)
            return
        if token != self.tokens.get(kind):
            return
        if kind == 'export':
            self.export.setEnabled(True)
        if error:
            self.message.setText('Operácia zlyhala: ' + error)
            return
        if kind == 'options':
            for control, values, title in [(self.recipe, data[0], 'Všetky recepty'), (self.view, data[1], 'Všetky pohľady')]:
                selected = control.currentData()
                control.clear()
                control.addItem(title, '')
                for value in values:
                    control.addItem(self._view_name(value) if control is self.view else str(value), value)
                control.setCurrentIndex(max(0, control.findData(selected)))
            self._update_filter_summary()
        elif kind == 'rows':
            self.rows = data[:self._page_size]
            self.history_group.setExclusive(False)
            for index, card in enumerate(self.cards):
                card.setChecked(False)
                card.setVisible(index < len(self.rows))
                if index < len(self.rows):
                    record = self.rows[index]
                    card.set_record(record, self._view_name(metadata(record).get('view_name') or record.get('view_id')))
            self.history_group.setExclusive(True)
            self.empty_history.setVisible(not self.rows)
            self.previous.setEnabled(len(self.cursors) > 1)
            self.next.setEnabled(len(data) > self._page_size)
            self.page_label.setText(f'Strana {len(self.cursors)}')
            self.message.setText(f'{len(self.rows)} kontrol na strane' if self.rows else 'Žiadne kontroly pre zvolené filtre.')
            self._choose_row(0 if self.rows else -1)
            if self.rows:
                self.cards[0].setChecked(True)
        elif kind == 'image':
            image, thumbnail = data
            self._has_image = image is not None
            self.overlays = []
            self.canvas.set_pixmap(QPixmap.fromImage(image) if image is not None else None)
            note = 'Snímka nie je dostupná. Uložené metriky zostávajú dostupné.' if image is None else ('Zobrazená uložená JPEG snímka.' if thumbnail else '')
            if not any('history_overlays' in report for report in getattr(self, 'reports', [])):
                note += ' Historická geometria v tomto zázname nie je uložená.'
            elif any(str(report.get('status')).lower() == 'nok' for report in self.reports) and not any(
                    item.get('error') for report in self.reports for item in report.get('history_overlays', [])):
                note += ' Chyby zvýrazňujú kontrolovanú ROI; presná lokalizácia chyby nie je uložená.'
            self.image_note.setText(note)
            self.image_note.setVisible(bool(note))
            self.message.setText(f'{len(self.rows)} kontrol na strane' + (' · ' + note if note else ''))
            self.message.setToolTip(self.message.text())
            self._draw()
        elif kind == 'export':
            self.message.setText(f'CSV export dokončený: {data} kontrol.')

"""Operator history browser. Workers return data/QImage, never QPixmap/widgets."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QDate, QPointF
from PySide6.QtGui import QImageReader, QPixmap, QColor, QPen, QPolygonF, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QDateEdit, QSplitter, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QCheckBox, QFileDialog,
)

from app.services.results_browser import query_results, metadata, filter_options, export_results
from app.ui.image_canvas import ImageView, ImageNavigationToolbar, InteractionMode
from app.ui.responsive import WrapLayout


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
    PAGE_SIZE = 50

    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.jobs, self.tokens = {}, {}
        self._pending_image = None
        self.rows, self.cursors = [], [None]
        self.meta, self.overlays = {}, []
        self._has_image = False
        layout = QVBoxLayout(self)
        filters = WrapLayout()
        self.start = QDateEdit(QDate.currentDate().addDays(-7))
        self.end = QDateEdit(QDate.currentDate())
        for label, control in [('Od', self.start), ('Do', self.end)]:
            control.setCalendarPopup(True)
            control.setDisplayFormat('dd.MM.yyyy')
            filters.addWidget(QLabel(label))
            filters.addWidget(control)
        self.recipe, self.view, self.status = QComboBox(), QComboBox(), QComboBox()
        self.recipe.addItem('Všetky recepty', '')
        self.view.addItem('Všetky pohľady', '')
        for label, value in [('Všetky stavy', None), ('OK', 1), ('NOK', 0)]:
            self.status.addItem(label, value)
        for widget in (self.recipe, self.view, self.status):
            filters.addWidget(widget)
        refresh = QPushButton('Vyhľadať')
        refresh.setProperty('role', 'primary')
        refresh.clicked.connect(self.reload)
        filters.addWidget(refresh)
        self.export = QPushButton('Export CSV')
        self.export.clicked.connect(self._export)
        filters.addWidget(self.export)
        layout.addLayout(filters)
        self.message = QLabel('Vyberte kontrolu zo zoznamu.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        notice = QLabel('História kontrol · Produkčné spúšťanie je dostupné v RUN.')
        notice.setProperty('role', 'secondary')
        layout.addWidget(notice)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Čas', 'Recept', 'Pohľad', 'Stav'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._select)
        left_layout.addWidget(self.table)
        paging = QHBoxLayout()
        self.previous, self.next = QPushButton('Predchádzajúce'), QPushButton('Ďalšie')
        self.previous.clicked.connect(self._previous)
        self.next.clicked.connect(self._next)
        paging.addWidget(self.previous)
        paging.addWidget(self.next)
        left_layout.addLayout(paging)
        splitter.addWidget(left)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.canvas = ImageView()
        navigation = ImageNavigationToolbar(self.canvas)
        navigation.mode_buttons[InteractionMode.DRAW].hide()
        center_layout.addWidget(navigation)
        toggles = QHBoxLayout()
        self.roi = QCheckBox('Zobraziť ROI')
        self.errors = QCheckBox('Zobraziť chyby')
        self.errors.setChecked(True)
        for checkbox in (self.roi, self.errors):
            checkbox.toggled.connect(self._draw)
            toggles.addWidget(checkbox)
        center_layout.addLayout(toggles)
        self.image_note = QLabel('')
        self.image_note.setWordWrap(True)
        center_layout.addWidget(self.image_note)
        center_layout.addWidget(self.canvas, 1)
        splitter.addWidget(center)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.result = QLabel('VÝSLEDOK KONTROLY —')
        self.result.setWordWrap(True)
        right_layout.addWidget(self.result)
        self.tool = QComboBox()
        self.tool.currentIndexChanged.connect(self._draw)
        right_layout.addWidget(self.tool)
        self.summary = QLabel('')
        self.summary.setWordWrap(True)
        right_layout.addWidget(self.summary)
        details = QCheckBox('Podrobné metriky a uložené prahy')
        right_layout.addWidget(details)
        self.details = QTableWidget(0, 3)
        self.details.setHorizontalHeaderLabels(['Nástroj', 'Parameter', 'Hodnota'])
        self.details.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.details.verticalHeader().hide()
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.details.hide()
        details.toggled.connect(self.details.setVisible)
        right_layout.addWidget(self.details, 1)
        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([400, 950, 300])
        splitter.setStretchFactor(1, 1)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)

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
        self._submit('options', lambda: filter_options(self.db_path))
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
        self._load()

    def _load(self):
        filters, cursor = dict(self.filters), self.cursors[-1]
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.message.setText('Načítavam výsledky…')
        self._submit('rows', lambda: query_results(self.db_path, filters, cursor, self.PAGE_SIZE+1))

    def _previous(self):
        if len(self.cursors) > 1:
            self.cursors.pop()
            self._load()

    def _next(self):
        if self.rows:
            self.cursors.append(self.rows[-1]['id'])
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
        self.image_note.setText('Vyberte kontrolu zo zoznamu.')
        row = self.table.currentRow()
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
        self.result.setText(('OK' if record['ok'] else 'NOK') + '\n' +
                            str(self.meta.get('view_name') or record.get('view_id') or '') + '\n' +
                            f"Čas kontroly: {self.meta.get('cycle_time_ms', '—')} ms")
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
            for entry in report.get('history_overlays', []):
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

    def _export(self):
        try:
            filters = self._filters()
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
        self._submit('export', lambda: export_results(self.db_path, filters, path))

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
                    control.addItem(str(value), value)
                control.setCurrentIndex(max(0, control.findData(selected)))
        elif kind == 'rows':
            self.table.blockSignals(True)
            self.rows = data[:self.PAGE_SIZE]
            self.table.setRowCount(len(self.rows))
            for row, record in enumerate(self.rows):
                values = [datetime.fromtimestamp(record['ts_ms']/1000).strftime('%d.%m. %H:%M:%S'), record['recipe'], record['view_id'], 'OK' if record['ok'] else 'NOK']
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value or '—'))
                    if column == 3:
                        item.setForeground(QColor('#24c76a' if record['ok'] else '#ff5353'))
                    self.table.setItem(row, column, item)
            self.table.blockSignals(False)
            self.previous.setEnabled(len(self.cursors) > 1)
            self.next.setEnabled(len(data) > self.PAGE_SIZE)
            self.message.setText(f'Strana {len(self.cursors)} · {len(self.rows)} kontrol' if self.rows else 'Žiadne kontroly pre zvolené filtre.')
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._select()
            if self.rows:
                self.table.selectRow(0)
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
                note += ' Nástroj neuložil presnú lokalizáciu chyby; môžete zobraziť kontrolované ROI.'
            self.image_note.setText(note)
            self._draw()
        elif kind == 'export':
            self.message.setText(f'CSV export dokončený: {data} kontrol.')

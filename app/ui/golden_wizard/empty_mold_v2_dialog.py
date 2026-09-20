"""Dedicated V2 setup workspace using the existing ROI editor and recipe authorization."""
from copy import deepcopy
import json
import cv2
import numpy as np
from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap, QPainterPath, QPen, QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QTabWidget, QWidget, QComboBox, QMessageBox, QDialogButtonBox,
    QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QScrollArea, QFileDialog, QSlider)
from app.ui.roi_mask_editor import ROIEditor
from app.ui.golden_wizard.presence_v2_sample_capture_dialog import PresenceV2SampleCaptureDialog
from app.ui.golden_wizard.tool_config_panel import CollapsibleSection
from app.ui.golden_wizard.style import GOLDEN_WIZARD_STYLE
from app.services.empty_mold_v2.evaluator import DEFAULTS, settings, evaluate
from app.services.empty_mold_v2.regions import zones_for, annotation_masks, shape_mask, OUTSIDE
from app.services.empty_mold_v2.storage import load_model
from app.services.empty_mold_v2.workflow import Workflow


def pixmap(frame):
    array = np.ascontiguousarray(frame)
    fmt = QImage.Format_Grayscale8 if array.ndim == 2 else QImage.Format_BGR888
    return QPixmap.fromImage(QImage(array.data, array.shape[1], array.shape[0], array.strides[0], fmt).copy())


def set_region_background(editor, frame, tool, polygons=()):
    """Keep pixels pristine; cosmetic scene paths stay continuous at any zoom."""
    editor.set_background(pixmap(frame))
    scene = editor._view.scene()
    zones = zones_for(tool, frame.shape[:2])
    main = shape_mask(frame.shape[:2], tool.roi)
    valid = np.logical_or.reduce(list(zones.values()))
    masks = [(main, QColor('#53b9ff'))]
    masks += [(mask, QColor('#ffd64a')) for name, mask in zones.items() if name != OUTSIDE]
    masks.append((main & ~valid, QColor('#ff6868')))
    def add_path(points, color):
        if len(points) < 2:
            return
        path = QPainterPath()
        path.moveTo(float(points[0][0]), float(points[0][1]))
        for x, y in points[1:]:
            path.lineTo(float(x), float(y))
        path.closeSubpath()
        pen = QPen(color)
        pen.setWidthF(2.)
        pen.setCosmetic(True)
        item = scene.addPath(path, pen)
        item.setZValue(5)
        item.setAcceptedMouseButtons(Qt.NoButton)
        item.setToolTip('Modrá: hlavná ROI · žltá: kavita · červená: ignore · fialová: NOK anotácia')
    for mask, color in masks:
        contours, _ = cv2.findContours(mask.astype('uint8'), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            add_path(contour[:, 0, :], color)
    for polygon in polygons:
        add_path(polygon, QColor('#ff58e8'))


class EmptyMoldV2CaptureDialog(PresenceV2SampleCaptureDialog):
    def done(self, result):
        # QDialog.accept/reject can hide without closeEvent; stop V2 capture explicitly.
        self._stop_auto()
        super().done(result)


class AnnotationDialog(QDialog):
    def __init__(self, frame, tool, polygons=(), parent=None):
        super().__init__(parent)
        self.setObjectName('goldenWizard')
        self.setStyleSheet(GOLDEN_WIZARD_STYLE)
        self.setWindowTitle('NOK ground truth – polygóny skutočných defektov')
        self.resize(1000, 750)
        self.polygons = deepcopy(list(polygons))
        self.frame, self.tool = frame, tool
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Nakreslite polygón, dokončite dvojklikom a pridajte ho. Možno označiť viac defektov.'))
        self.editor = ROIEditor(self)
        layout.addWidget(self.editor, 1)
        self.list = QListWidget()
        self.list.setMaximumHeight(100)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        for title, callback in [('Pridať polygón', self.add), ('Odstrániť vybraný', self.remove)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        set_region_background(self.editor, self.frame, self.tool, self.polygons)
        self.list.clear()
        self.list.addItems([f'Defekt {i+1}: {len(p)} vrcholov' for i, p in enumerate(self.polygons)])

    def add(self):
        roi = self.editor.roi_data()
        try:
            if roi.get('shape') != 'polygon':
                raise ValueError('Vyberte nástroj Polygón.')
            annotation_masks([roi['points']], zones_for(self.tool, self.frame.shape[:2]))
            self.polygons.append(roi['points'])
            self.refresh()
            self.editor.set_roi_data({})
        except ValueError as exc:
            QMessageBox.warning(self, 'Anotácia', str(exc))

    def remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self.polygons.pop(row)
            self.refresh()

    def finish(self):
        if not self.polygons:
            QMessageBox.warning(self, 'Anotácia', 'Pridajte aspoň jeden NOK polygón.')
            return
        self.accept()


class _Signals(QObject):
    done = Signal(object, str)


class _Work(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function, self.signals = function, _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function(), '')
        except Exception as exc:
            self.signals.done.emit(None, str(exc))


class EmptyMoldV2Dialog(QDialog):
    def __init__(self, tool, store, golden, signature, capture, authorize, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Kontrola prázdnej formy V2 – dataset a model')
        self.setObjectName('goldenWizard')
        self.setStyleSheet(GOLDEN_WIZARD_STYLE)
        self.resize(1180, 850)
        self.tool, self.store, self.golden = tool, store, golden
        self.signature, self.capture, self.authorize = signature, capture, authorize
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.workflow = Workflow(tool, store, golden, signature, lambda: self._authorized)
        self._authorized = False
        layout = QVBoxLayout(self)
        self.notice = QLabel('Golden → hlavná ROI → kavity → ignore → OK zber → model → NOK anotácie → odporúčania → validácia → aktivácia → uloženie receptu')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._regions_tab()
        self._dataset_tab()
        self._model_tab()
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.close_button = QPushButton('Zavrieť a ponechať v rozpracovanom recepte')
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.close_button)
        self.refresh()

    def guard(self):
        self._authorized = bool(self.authorize())
        return self._authorized

    def action(self, function, done=None, *, background=False):
        if self.busy or not self.guard():
            return
        if not background:
            try:
                value = function()
                if done:
                    done(value)
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, 'Empty Mold V2', str(exc))
            return
        self.busy = True
        self.tabs.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status.setText('Prebieha offline výpočet…')
        self.job = _Work(function)
        def finished(value, error):
            self.busy = False
            self.tabs.setEnabled(True)
            self.close_button.setEnabled(True)
            if error:
                QMessageBox.warning(self, 'Empty Mold V2', error)
            elif done:
                done(value)
            self.refresh()
        self.job.signals.done.connect(finished)
        self.pool.start(self.job)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def _button(self, layout, title, function, done=None, background=False):
        button = QPushButton(title)
        button.clicked.connect(lambda: self.action(function, done, background=background))
        layout.addWidget(button)
        return button

    def _regions_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Hlavnú ROI a ignore zóny kreslite v Golden Wizarde. Tu pridajte kavity; OUTSIDE sa vypočíta automaticky.'))
        layout.addWidget(QLabel('Modrá: hlavná ROI · žltá: kavity · červená: ignore · fialová: NOK anotácie'))
        self.region_editor = ROIEditor()
        layout.addWidget(self.region_editor, 1)
        self.cavities = QListWidget()
        self.cavities.setMaximumHeight(100)
        self.cavities.currentRowChanged.connect(self.select_cavity)
        layout.addWidget(self.cavities)
        row = QHBoxLayout()
        self._button(row, 'Pridať kavitu', self.add_cavity)
        self._button(row, 'Nahradiť vybranú kavitu', self.replace_cavity)
        self._button(row, 'Odstrániť kavitu', self.remove_cavity)
        layout.addLayout(row)
        self.tabs.addTab(page, 'Kavity')

    def select_cavity(self, index):
        cavities = self.tool.params.values.get('cavities', [])
        self.region_editor.set_roi_data(cavities[index]['roi'] if 0 <= index < len(cavities) else {})

    def change_cavities(self, cavities):
        previous = self.tool.params.values.get('cavities', [])
        self.tool.params.values['cavities'] = cavities
        try:
            self.workflow = Workflow(self.tool, self.store, self.golden, self.signature, lambda: self._authorized)
        except Exception:
            self.tool.params.values['cavities'] = previous
            raise

    def add_cavity(self):
        cavities = deepcopy(self.tool.params.values.get('cavities', []))
        names = {c['name'] for c in cavities}
        index = 1
        while f'K{index}' in names:
            index += 1
        cavities.append({'name': f'K{index}', 'roi': self.region_editor.roi_data()})
        self.change_cavities(cavities)

    def replace_cavity(self):
        row = self.cavities.currentRow()
        if row < 0:
            raise ValueError('Vyberte kavitu.')
        cavities = deepcopy(self.tool.params.values.get('cavities', []))
        cavities[row]['roi'] = self.region_editor.roi_data()
        self.change_cavities(cavities)

    def remove_cavity(self):
        row = self.cavities.currentRow()
        if row >= 0:
            cavities = deepcopy(self.tool.params.values.get('cavities', []))
            cavities.pop(row)
            self.change_cavities(cavities)

    def _dataset_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Zber OK vzoriek: odporúčame najmenej 30, ideálne 50+. Jeden capture = jeden celý zarovnaný frame pre všetky zóny.'))
        self.split = QComboBox()
        self.split.addItem('Tréning', 'training')
        self.split.addItem('Nezávislá validácia', 'validation')
        layout.addWidget(self.split)
        row = QHBoxLayout()
        self._button(row, 'Zber OK vzoriek', lambda: self.collect(False))
        self._button(row, 'Zber NOK vzorky + polygóny', lambda: self.collect(True))
        layout.addLayout(row)
        self.samples = QTableWidget(0, 4)
        self.samples.setHorizontalHeaderLabels(['Čas', 'Stav', 'Kontext', 'ID'])
        self.samples.horizontalHeader().setStretchLastSection(True)
        self.samples.setColumnWidth(0, 220)
        self.samples.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.samples.setSelectionMode(QAbstractItemView.SingleSelection)
        self.samples.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.samples.cellDoubleClicked.connect(lambda *_: self.action(self.debug_sample))
        layout.addWidget(self.samples, 1)
        row = QHBoxLayout()
        self._button(row, 'Otvoriť / diagnostika', self.debug_sample)
        self._button(row, 'Prijať ako OK', lambda: self.review(False))
        self._button(row, 'Anotovať / prijať ako NOK', lambda: self.review(True))
        self._button(row, 'Zamietnuť', self.reject_sample)
        self._button(row, 'Export datasetu', self.export_dataset)
        layout.addLayout(row)
        self.tabs.addTab(page, 'Vzorky a kandidáti')

    def collect(self, nok):
        if nok:
            frame = self.capture()
            if frame is None:
                return
            dialog = AnnotationDialog(frame, self.tool, parent=self)
            if dialog.exec() == QDialog.Accepted:
                self.workflow.add_sample(frame, self.split.currentData() + '_nok', dialog.polygons)
        else:
            dialog = EmptyMoldV2CaptureDialog(title='Zber OK – celý zarovnaný frame',
                         capture_fn=self.capture, crop_fn=lambda frame: frame, parent=self)
            dialog._target.setValue(50)
            if dialog.exec() == QDialog.Accepted:
                errors = []
                for frame in dialog.samples():
                    try:
                        self.workflow.add_sample(frame, self.split.currentData() + '_ok')
                    except ValueError as exc:
                        errors.append(str(exc))
                if errors:
                    QMessageBox.warning(self, 'Zber', f'{len(errors)} snímok nebolo prijatých: {errors[0]}')

    def export_dataset(self):
        from app.services.empty_mold_v2.export import export_dataset
        path, _ = QFileDialog.getSaveFileName(self, 'Export V2 datasetu', 'empty_mold_v2.zip', 'ZIP (*.zip)')
        if path:
            export_dataset(self.store, path)

    def selected_sample(self):
        row = self.samples.currentRow()
        if not 0 <= row < len(self.sample_rows):
            raise ValueError('Vyberte vzorku.')
        return self.sample_rows[row]

    def review(self, nok):
        sample = self.selected_sample()
        polygons = []
        if nok:
            dialog = AnnotationDialog(self.store.frame(sample), self.tool, sample['annotations'], self)
            if dialog.exec() != QDialog.Accepted:
                return
            polygons = dialog.polygons
        self.workflow.review(sample['id'], self.split.currentData() + ('_nok' if nok else '_ok'), polygons)

    def reject_sample(self):
        sample = self.selected_sample()
        self.workflow.review(sample['id'], 'rejected')

    def _model_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self._button(row, 'Vytvoriť variation model', lambda: self.workflow.build(), background=True)
        self._button(row, 'Mapa variability', self.heatmap)
        layout.addLayout(row)
        self.versions = QComboBox()
        layout.addWidget(self.versions)
        self._button(layout, 'Vybrať uloženú verziu na testovanie', self.select_model)
        self.fields = {}
        main = QFormLayout()
        for key, title in [('sensitivity', 'Citlivosť (1–100)'), ('min_blob_area', 'Minimálna plocha defektu'),
                           ('max_blob_count', 'Povolený počet blobov')]:
            self.add_field(main, key, title)
        sensitivity_slider = QSlider(Qt.Horizontal)
        sensitivity_slider.setRange(1, 100)
        sensitivity_slider.setValue(int(settings(self.tool)['sensitivity']))
        sensitivity_slider.valueChanged.connect(self.fields['sensitivity'].setValue)
        self.fields['sensitivity'].valueChanged.connect(sensitivity_slider.setValue)
        main.addRow('Vyššie = citlivejšie', sensitivity_slider)
        self.sensitivity_slider = sensitivity_slider
        self.polarity = QComboBox()
        for label, value in [('Oboje', 'both'), ('Svetlejší', 'bright'), ('Tmavší', 'dark')]:
            self.polarity.addItem(label, value)
        self.polarity.setCurrentIndex(max(0, self.polarity.findData(settings(self.tool)['polarity'])))
        self.polarity.currentIndexChanged.connect(lambda: self.set_parameter('polarity', self.polarity.currentData()))
        main.addRow('Polarita', self.polarity)
        layout.addLayout(main)
        advanced = QWidget()
        form = QFormLayout(advanced)
        for key, title in [('anomaly_multiplier', 'Anomaly multiplier (0 = citlivosť)'),
                           ('max_blob_area', 'Max. blob area (0 = bez limitu)'),
                           ('mean_anomaly_min', 'Min. priemerná anomália'), ('max_anomaly_min', 'Min. maximálna anomália'),
                           ('morph_open', 'OPEN polomer'), ('morph_close', 'CLOSE polomer'),
                           ('erode', 'Erode polomer'), ('dilate', 'Dilate polomer'),
                           ('ignore_border', 'Ignorovať okraj (px)'), ('variability_floor', 'Variability floor'),
                           ('variability_cap', 'Variability cap (0 = bez limitu)')]:
            self.add_field(form, key, title)
        normalize = QCheckBox('Globálne vyrovnanie jasu (odčíta medián platnej ROI)')
        normalize.setChecked(settings(self.tool)['normalize'])
        normalize.toggled.connect(lambda value: self.set_parameter('normalize', value))
        form.addRow(normalize)
        self.zone_overrides = QTableWidget(0, 2)
        self.zone_overrides.setHorizontalHeaderLabels(['Zóna', 'Anomaly threshold (0 = spoločný)'])
        form.addRow(self.zone_overrides)
        apply_zones = QPushButton('Použiť prahy zón')
        apply_zones.clicked.connect(lambda: self.action(self.apply_zone_overrides))
        form.addRow(apply_zones)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(advanced)
        scroll.setMaximumHeight(300)
        layout.addWidget(CollapsibleSection('Pokročilé nastavenia', scroll, expanded=False))
        row = QHBoxLayout()
        self._button(row, 'Navrhnúť nastavenia', lambda: self.workflow.suggest(), self.show_recommendation, True)
        self._button(row, 'Použiť odporúčané', lambda: self.workflow.apply_recommended())
        self._button(row, 'Vrátiť predchádzajúce', lambda: self.workflow.undo())
        self._button(row, 'Otestovať vzorky', lambda: self.workflow.test(), self.show_validation, True)
        layout.addLayout(row)
        self.comparison = QLabel('Odporúčania nemenia nastavenia. Test používa iba nezávislé validačné vzorky.')
        self.comparison.setWordWrap(True)
        layout.addWidget(self.comparison)
        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(['Vzorka', 'Očakávané', 'Výsledok', 'Zóny / chyby'])
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.cellDoubleClicked.connect(self.open_validation_sample)
        layout.addWidget(self.results)
        self._button(layout, 'Explicitne aktivovať validovanú verziu', self.activate)
        self.tabs.addTab(page, 'Model, ladenie a validácia')

    def add_field(self, form, key, title):
        integer = key in ('sensitivity', 'min_blob_area', 'max_blob_count', 'max_blob_area',
                          'morph_open', 'morph_close', 'erode', 'dilate', 'ignore_border')
        field = QSpinBox() if integer else QDoubleSpinBox()
        field.setRange(1 if key in ('sensitivity', 'min_blob_area') else 0, 100 if key == 'sensitivity' else 10000000)
        if key in ('morph_open', 'morph_close', 'erode', 'dilate', 'ignore_border'):
            field.setMaximum(32)
        field.setValue(settings(self.tool)[key])
        field.setKeyboardTracking(False)
        field.valueChanged.connect(lambda value: self.set_parameter(key, value))
        self.fields[key] = field
        form.addRow(title, field)

    def set_parameter(self, key, value):
        if self.busy or not self.guard():
            self.refresh()
            return
        self.tool.params.values[key] = value
        self.tool.thresholds.values.pop(key, None)
        self.workflow.validation = None

    def apply_zone_overrides(self):
        overrides = {}
        for row in range(self.zone_overrides.rowCount()):
            value = self.zone_overrides.cellWidget(row, 1).value()
            if value:
                overrides[self.zone_overrides.item(row, 0).text()] = value
        self.set_parameter('zone_thresholds', overrides)

    def refresh(self):
        self.cavities.blockSignals(True)
        self.cavities.clear()
        self.cavities.addItems([c['name'] for c in self.tool.params.values.get('cavities', [])])
        self.cavities.blockSignals(False)
        set_region_background(self.region_editor, self.golden, self.tool)
        self.sample_rows = self.store.samples()
        self.samples.setRowCount(len(self.sample_rows))
        for row, sample in enumerate(self.sample_rows):
            for col, value in enumerate([sample['created_at'], sample['state'],
                                         'aktuálny' if sample['metadata'].get('context') == self.workflow.context else 'iný', sample['id']]):
                self.samples.setItem(row, col, QTableWidgetItem(str(value)))
        selected = self.versions.currentData()
        self.versions.clear()
        active = self.tool.params.values.get('v2_active_model')
        for version in self.store.models():
            label = 'AKTÍVNY V ROZPRACOVANOM RECEPTE' if version['artifact_path'] == active else 'neaktívny'
            self.versions.addItem(f"{version['created_at']} · {version['id'][:8]} · {label}", version['artifact_path'])
        if selected:
            self.versions.setCurrentIndex(max(0, self.versions.findData(selected)))
        p = settings(self.tool)
        for key, field in self.fields.items():
            field.blockSignals(True)
            field.setValue(p[key])
            field.blockSignals(False)
        self.sensitivity_slider.blockSignals(True)
        self.sensitivity_slider.setValue(int(p['sensitivity']))
        self.sensitivity_slider.blockSignals(False)
        self.zone_overrides.setRowCount(len(self.workflow.zones))
        for row, name in enumerate(self.workflow.zones):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.zone_overrides.setItem(row, 0, item)
            field = QDoubleSpinBox()
            field.setRange(0, 1000)
            field.setValue(p['zone_thresholds'].get(name, 0))
            self.zone_overrides.setCellWidget(row, 1, field)
        counts = {state: sum(s['state'] == state for s in self.sample_rows) for state in sorted({s['state'] for s in self.sample_rows})}
        self.status.setText(' | '.join(f'{k}: {v}' for k, v in counts.items()) + '\nRUN používa explicitne aktivovanú verziu uloženú v recepte.')

    def select_model(self):
        path = self.versions.currentData()
        if not path:
            raise ValueError('Nie je uložená verzia.')
        self.workflow.candidate = load_model(path)
        self.workflow.validation = None

    def show_recommendation(self, result):
        changes = [f"{key}: {result['current'][key]} → {result['recommended'][key]}"
                   for key in ('sensitivity', 'min_blob_area', 'morph_close', 'anomaly_multiplier')]
        self.comparison.setText('Aktuálne → Odporúčané\n' + '\n'.join(changes) + '\nTréningové hodnotenie: ' + self.summary(result['summary']))

    @staticmethod
    def summary(report):
        return ' | '.join(f'{key}: {report[key]}' for key in ('ok_correct', 'nok_correct', 'false_nok', 'false_ok', 'missed_polygons', 'false_positive_blobs'))

    def show_validation(self, report):
        self.comparison.setText('Nezávislá validácia: ' + self.summary(report))
        self.validation_rows = report['samples']
        self.results.setRowCount(len(self.validation_rows))
        for row, sample in enumerate(self.validation_rows):
            for col, value in enumerate([sample['id'], sample['expected'], sample['classification'], json.dumps(sample['zones'])]):
                self.results.setItem(row, col, QTableWidgetItem(str(value)))

    def open_validation_sample(self, row, _column):
        ident = self.validation_rows[row]['id']
        sample = next(s for s in self.sample_rows if s['id'] == ident)
        self.action(lambda: self.debug_sample(sample))

    def activate(self):
        ident = self.workflow.activate()
        QMessageBox.information(self, 'Aktivácia V2', f'Verzia {ident[:8]} je vybraná v rozpracovanom recepte. Uložte recept existujúcim postupom; tým sa aktivovaná verzia sprístupní RUN.')

    def show_images(self, title, images):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1000, 750)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        if any(len(entry) > 2 for entry in images):
            layout.addWidget(QLabel('Modrá: hlavná ROI · žltá: kavity · červená: ignore · fialová: NOK anotácie'))
        for entry in images:
            label, image = entry[:2]
            editor = ROIEditor()
            if len(entry) > 2:
                set_region_background(editor, image, self.tool, entry[2])
            else:
                editor.set_background(pixmap(image))
            tabs.addTab(editor, label)
        dialog.exec()

    def heatmap(self):
        model = self.workflow.candidate_model()
        mask = np.logical_or.reduce(list(self.workflow.zones.values()))
        values = model.variability
        upper = max(float(np.percentile(values[mask], 99)), 1.)
        heat = cv2.applyColorMap(np.uint8(np.clip(values / upper * 255, 0, 255)), cv2.COLORMAP_TURBO)
        base = cv2.cvtColor(self.golden, cv2.COLOR_GRAY2BGR) if self.golden.ndim == 2 else self.golden.copy()
        overlay = base.copy()
        overlay[mask] = cv2.addWeighted(base, .4, heat, .6, 0)[mask]
        self.show_images('Mapa variability – modrá stabilná, červená variabilná', [('Variabilita', overlay)])

    def debug_sample(self, sample=None):
        sample = sample or self.selected_sample()
        frame = self.store.frame(sample)
        images = [('Originál + zóny + GT', frame, sample['annotations']), ('Golden', self.golden)]
        if self.workflow.candidate is not None and sample['metadata'].get('context') == self.workflow.context:
            model = self.workflow.candidate_model()
            p = settings(self.tool)
            zones = zones_for(self.tool, frame.shape, ignore_border=p['ignore_border'])
            result = evaluate(frame, model, zones, p)
            images += [('Anomaly map', cv2.applyColorMap(np.uint8(np.clip(result['anomaly'] * 20, 0, 255)), cv2.COLORMAP_TURBO)),
                       ('Binary maska', result['binary'] * 255)]
            overlay = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
            for blob in result['blobs']:
                x, y, w, h = (blob[k] for k in ('x', 'y', 'width', 'height'))
                cv2.rectangle(overlay, (x, y), (x+w-1, y+h-1), (0, 0, 255), 2)
            images.append(('Bloby', overlay, sample['annotations']))
        self.show_images('Vzorka ' + sample['id'], images)

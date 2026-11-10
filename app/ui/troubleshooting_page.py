"""Extensible diagnostic page with a read-only camera position module."""
import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QStackedWidget, QFrame, QSizePolicy, QButtonGroup, QToolButton)
from app.ui.image_canvas import ImageView
from app.ui.camera_correction_overlay import CorrectionImageView
from app.ui.results_widgets import ResultsOverlay


def _pixmap(image):
    image = np.ascontiguousarray(image, dtype=np.uint8)
    if image.ndim == 2:
        h, w = image.shape
        q = QImage(image.data, w, h, image.strides[0], QImage.Format_Grayscale8)
    else:
        image = np.ascontiguousarray(image[:, :, :3])
        h, w = image.shape[:2]
        q = QImage(image.data, w, h, image.strides[0], QImage.Format_BGR888)
    return QPixmap.fromImage(q.copy())


class TroubleshootingPage(QWidget):
    deactivated = Signal()

    def __init__(self, service, submit, parent=None):
        super().__init__(parent)
        self.service, self.submit = service, submit
        self.active = False
        self.busy = False
        self.reference = None
        self.current = None
        self.result = None
        self._generation = 0
        self._request_generation = 0
        self._blink_current = False
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self.capture)
        self.blink_timer = QTimer(self)
        self.blink_timer.setInterval(400)
        self.blink_timer.timeout.connect(self._blink)

        self.setObjectName('diagnosticsPage')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel('Overenie polohy kamery')
        title.setProperty('role', 'appTitle')
        header.addWidget(title)
        header.addStretch(1)
        self.views = QComboBox()
        self.views.setMinimumWidth(180)
        self.views.setMaximumWidth(280)
        self.views.currentIndexChanged.connect(self._view_changed)
        header.addWidget(self.views)
        self.details_button = QPushButton('Podrobnosti')
        header.addWidget(self.details_button)
        layout.addLayout(header)
        context = QHBoxLayout()
        self.recipe_label = QLabel('Aktívny recept: –')
        self.recipe_label.setProperty('role', 'secondary')
        context.addWidget(self.recipe_label, 1)
        context.addWidget(QLabel('Kontroly tejto kamery sú pozastavené'))
        layout.addLayout(context)
        self.compact_status = QLabel('Zhotovte novú snímku.')
        self.compact_status.setObjectName('diagnosticStatus')
        self.compact_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        status_row = QHBoxLayout()
        status_row.addWidget(self.compact_status, 1)
        self.compact_directions = QLabel()
        self.compact_directions.setStyleSheet('color: #ffc247;')
        self.compact_directions.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        status_row.addWidget(self.compact_directions, 1)
        layout.addLayout(status_row)
        display = QHBoxLayout()
        self.display_mode = QComboBox(self)
        self.display_mode.addItems(['VEDĽA SEBA', 'PREPÍNANIE'])
        self.display_mode.hide()
        self.display_group = QButtonGroup(self)
        for index, title in enumerate(['Vedľa seba', 'Prepínanie']):
            button = QPushButton(title)
            button.setCheckable(True); button.setChecked(index == 0)
            self.display_group.addButton(button, index)
            button.clicked.connect(lambda checked=False, i=index: self.display_mode.setCurrentIndex(i))
            display.addWidget(button)
        self.display_mode.currentIndexChanged.connect(self._display_changed)
        display.addStretch(1)
        self.arrow_toggle = QPushButton('Šípky korekcie')
        self.arrow_toggle.setCheckable(True); self.arrow_toggle.setChecked(True)
        self.arrow_toggle.toggled.connect(self._refresh_arrows)
        display.addWidget(self.arrow_toggle)
        self.fit_button = QPushButton('Prispôsobiť')
        self.fit_button.clicked.connect(self._fit_images)
        display.addWidget(self.fit_button)
        layout.addLayout(display)
        self.images = QStackedWidget()
        pair = QWidget()
        pair_layout = QHBoxLayout(pair)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setSpacing(12)
        self.golden_view, self.current_view, self.blink_view = ImageView(), CorrectionImageView(), CorrectionImageView()
        for title, view in [('GOLDEN · RAW referencia', self.golden_view), ('AKTUÁLNA · RAW snímka', self.current_view)]:
            column = QWidget()
            col = QVBoxLayout(column)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(6)
            col.addWidget(QLabel(title))
            col.addWidget(view, 1)
            pair_layout.addWidget(column, 1)
        blink = QWidget()
        blink_layout = QVBoxLayout(blink)
        blink_layout.setContentsMargins(0, 0, 0, 0)
        blink_layout.setSpacing(6)
        self.blink_label = QLabel('GOLDEN')
        blink_layout.addWidget(self.blink_label)
        blink_layout.addWidget(self.blink_view, 1)
        self.images.addWidget(pair)
        self.images.addWidget(blink)
        self.images.setMinimumHeight(180)
        self.images.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(self.images, 1)
        for view in (self.golden_view, self.current_view, self.blink_view):
            view.setBackgroundBrush(QColor('#11161b'))
            view.setStyleSheet('background: #11161b;')
        measurements = QHBoxLayout()
        self.metric_values, self.metric_limits = [], []
        for title in ['Posun X', 'Posun Y', 'Otočenie', 'Spoľahlivosť zhody']:
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(QLabel(title))
            value = QLabel('—'); value.setProperty('role', 'metricValue')
            limit = QLabel('—'); limit.setProperty('role', 'secondary')
            column.addWidget(value); column.addWidget(limit)
            measurements.addLayout(column, 1)
            self.metric_values.append(value); self.metric_limits.append(limit)
        layout.addLayout(measurements)
        controls = QHBoxLayout()
        self.capture_mode = QComboBox(self)
        self.capture_mode.addItems(['Manuálne', 'Automaticky'])
        self.capture_mode.hide()
        self.automatic_button = QPushButton('Automatické snímanie')
        self.automatic_button.setCheckable(True)
        self.automatic_button.toggled.connect(lambda checked: self.capture_mode.setCurrentIndex(int(checked)))
        self.capture_mode.currentIndexChanged.connect(self._automatic_changed)
        controls.addWidget(self.automatic_button)
        self.interval_label = QLabel('Rozostup:')
        controls.addWidget(self.interval_label)
        self.interval = QComboBox()
        for text, ms in [('0,5 s', 500), ('1 s', 1000), ('2 s', 2000), ('5 s', 5000)]:
            self.interval.addItem(text, ms)
        self.interval.setCurrentIndex(1)
        self.interval.currentIndexChanged.connect(self._automatic_changed)
        controls.addWidget(self.interval)
        controls.addStretch(1)
        self.capture_button = QPushButton('Zhotoviť snímku')
        self.capture_button.setProperty('role', 'primary')
        self.capture_button.clicked.connect(self.capture)
        controls.addWidget(self.capture_button)
        layout.addLayout(controls)
        self.detail_overlay = ResultsOverlay(self, 'Podrobnosti diagnostiky')
        self.details_button.clicked.connect(lambda: self.detail_overlay.open(self.details_button))
        self.golden_info, self.current_info = QWidget(), QWidget()
        left, right = QVBoxLayout(self.golden_info), QVBoxLayout(self.current_info)
        for col in (left, right):
            col.setContentsMargins(0, 0, 0, 0)
        self.limits_label = QLabel('Tolerancie receptu: –')
        self.limits_label.setWordWrap(True)
        left.addWidget(self.limits_label)
        self.metrics = QLabel('ΔX – | ΔY – | Δrotation – | Confidence –')
        self.status = QLabel('Zhotovte novú snímku.')
        self.directions = QLabel()
        for label in (self.metrics, self.status, self.directions):
            label.setWordWrap(True)
            right.addWidget(label)
        self.detail_overlay.controls.addWidget(self.golden_info)
        self.detail_overlay.controls.addWidget(self.current_info)
        note = QLabel('Pokyny platia pre pevnú formu a pohyb kamery v jej obrazovej rovine '
                      '(pohľad zozadu smerom na formu). ΔX/ΔY sú v súradniciach receptu. '
                      'Diagnostika nemení recept ani výsledky RUN.')
        note.setWordWrap(True)
        self.detail_overlay.controls.addWidget(note)
        self._border(self.golden_view, 'ok')
        self._automatic_changed()
        self._controls()

    def _fit_images(self):
        for view in (self.golden_view, self.current_view, self.blink_view):
            view.fit_image_to_view()

    def _refresh_summary(self):
        text = self.status.text()
        self.compact_status.setText(text.split('\n')[0])
        self.compact_status.setToolTip(text)
        color = '#e6e8eb'
        if not self.busy and self.result is not None:
            color = '#5cdb8c' if self.result.overall_ok is True else '#ff8585' if self.result.overall_ok is False else '#ffc247'
        self.compact_status.setStyleSheet(f'color: {color};')
        advice = self.directions.text()
        self.compact_directions.setText(advice.replace('\n', ' · '))
        self.compact_directions.setToolTip(advice)
        self.compact_directions.setVisible(bool(advice))
        result = self.result if not self.busy else None
        valid = result is not None and not result.invalid_reason
        values = [result.dx_px, result.dy_px, result.rotation_deg] if valid else [None]*3
        checks = [result.x_within_tolerance, result.y_within_tolerance, result.rotation_within_tolerance] if valid else [None]*3
        limits = self.reference.limits if self.reference is not None else None
        for i, (name, unit) in enumerate([('x', ' px'), ('y', ' px'), ('rotation', '°')]):
            value, check = values[i], checks[i]
            self.metric_values[i].setText('—' if value is None else f'{value:+.2f}{unit}')
            color = '#5cdb8c' if check is True else '#ff8585' if check is False else '#e6e8eb'
            self.metric_values[i].setStyleSheet(f'color: {color};')
            limit = getattr(limits, name, None)
            self.metric_limits[i].setText('Tolerancia —' if limits is None else 'Tolerancia chýba' if limit is None else f'Tolerancia ±{limit:g}{unit}')
            self.metric_limits[i].setStyleSheet('color: #ffc247;' if limits is not None and limit is None else '')
        confidence = result.confidence if result is not None else None
        self.metric_values[3].setText('—' if confidence is None else f'{confidence*100:.1f} %')
        self.metric_limits[3].setText('Zhoda s referenciou')

    @staticmethod
    def _border(view, status):
        color = {'ok': '#30b66b', 'nok': '#e35353', 'idle': '#657080'}[status]
        view.setFrameShape(QFrame.Box)
        view.setStyleSheet(f'background: #11161b; border: 2px solid {color};')

    def _controls(self):
        ready = self.active and not self.busy and self.reference is not None
        self.capture_button.setEnabled(ready)
        self.views.setEnabled(self.active and not self.busy)
        self._refresh_summary()

    def activate(self, recipe_name, view_id=None):
        if self.busy:
            return False
        self.active = True
        self._generation += 1
        self.recipe_label.setText(f'Aktívny recept: {recipe_name}')
        self.reference = self.current = self.result = None
        for view in (self.golden_view, self.current_view, self.blink_view):
            view.set_pixmap(None)
        self.metrics.setText('ΔX – | ΔY – | Δrotation – | Confidence –')
        self.directions.clear()
        self._refresh_arrows(clear=True)
        self.capture_timer.stop()
        self.blink_timer.stop()
        return self._submit('diagnostic_reference', lambda: self.service.open_reference(recipe_name, view_id, self.hardware_mode))

    def deactivate(self):
        was_active = self.active
        self.active = False
        self._refresh_arrows(clear=True)
        self._generation += 1
        self.capture_timer.stop()
        self.blink_timer.stop()
        self.capture_mode.setCurrentIndex(0)
        self._controls()
        if was_active:
            self.deactivated.emit()

    def _submit(self, kind, operation):
        self.busy = True
        self._request_generation = self._generation
        self.status.setText('Načítavam referenciu…' if kind == 'diagnostic_reference' else 'Snímam a vyhodnocujem…')
        self.directions.clear()
        self._refresh_arrows(clear=True)
        self._controls()
        if self.submit(kind, operation):
            return True
        self.busy = False
        self.status.setText('Kamera je obsadená. Skúste znova.')
        self._controls()
        return False

    def capture(self):
        if not self.active or self.busy or self.reference is None:
            return False
        self.capture_timer.stop()
        reference = self.reference
        return self._submit('diagnostic_capture', lambda: self.service.capture(reference, self.hardware_mode))

    def completed(self, kind, payload, error):
        self.busy = False
        if not self.active or self._request_generation != self._generation:
            self._controls()
            return
        if error is not None:
            self.result = None
            self.directions.clear()
            self._refresh_arrows(clear=True)
            self.metrics.setText('ΔX – | ΔY – | Δrotation – | Confidence –')
            self.status.setText(f'POLOHU SA NEPODARILO SPOĽAHLIVO VYHODNOTIŤ: {error}')
            self._border(self.current_view, 'idle')
            # Hardware errors require a deliberate restart, not automatic retries.
            self.capture_mode.setCurrentIndex(0)
            self._display_changed()
        elif kind == 'diagnostic_reference':
            self.reference = payload
            self.views.blockSignals(True)
            self.views.clear()
            for view in payload.recipe.views:
                self.views.addItem(view.name, view.id)
            self.views.setCurrentIndex(self.views.findData(payload.view.id))
            self.views.blockSignals(False)
            def fmt(value, unit):
                return 'chýba' if value is None else f'±{value:g}{unit}'
            limits = payload.limits
            self.limits_label.setText(f'Tolerancie receptu: X {fmt(limits.x, " px")} | Y {fmt(limits.y, " px")} | R {fmt(limits.rotation, "°")}')
            self.golden_view.set_pixmap(_pixmap(payload.raw_golden))
            self.status.setText('Zhotovte novú RAW snímku. Chýbajúce tolerancie sa nenahrádzajú.')
            self._display_changed()
        else:
            self.current, self.result = payload
            self.current_view.set_pixmap(_pixmap(self.current))
            self._render_result()
            self._display_changed()
        self._controls()
        self._schedule_capture()

    def _render_result(self):
        result = self.result
        def metric(name, value, unit, check):
            number = '–' if value is None else f'{value:+.2f}{unit}'
            state = 'NEVYHODNOTENÉ' if check is None else ('OK' if check else 'MIMO')
            return f'{name} {number} ({state})'
        confidence = '–' if result.confidence is None else f'{result.confidence:.3f}'
        self.metrics.setText(' | '.join([
            metric('ΔX', result.dx_px, ' px', result.x_within_tolerance),
            metric('ΔY', result.dy_px, ' px', result.y_within_tolerance),
            metric('Δrotation', result.rotation_deg, '°', result.rotation_within_tolerance),
            f'Confidence {confidence}']))
        if result.invalid_reason:
            text = ('POLOHU SA NEPODARILO SPOĽAHLIVO VYHODNOTIŤ\n' + result.invalid_reason +
                    '\nSkontrolujte správnu formu, referenciu v obraze, osvetlenie a výrazný mechanický posun.')
        elif result.overall_ok is None:
            text = 'POLOHA NIE JE ÚPLNE VYHODNOTENÁ – chýba tolerancia alebo meranie rotácie.'
        else:
            text = 'POLOHA KAMERY OK' if result.overall_ok else 'MIMO TOLERANCIE'
        self.status.setText(text)
        self.directions.setText('\n'.join(result.corrections) if not result.invalid_reason else '')
        self._border(self.current_view, self._current_status())
        self._refresh_arrows()
        self._refresh_summary()

    def _refresh_arrows(self, *args, clear=False):
        corrections = ()
        if not clear and self.active and not self.busy and self.arrow_toggle.isChecked() and self.result is not None and not self.result.invalid_reason:
            corrections = self.result.corrections
        self.current_view.set_corrections(corrections)
        self.blink_view.set_corrections(corrections if self._blink_current else ())

    def _current_status(self):
        return 'idle' if self.result is None or self.result.overall_ok is None else ('ok' if self.result.overall_ok else 'nok')

    def _automatic_changed(self):
        automatic = self.capture_mode.currentIndex() == 1
        self.automatic_button.setChecked(automatic)
        self.interval.setVisible(automatic)
        self.interval_label.setVisible(automatic)
        self.capture_timer.stop()
        self._schedule_capture()

    def _schedule_capture(self):
        if self.active and not self.busy and self.reference is not None and self.capture_mode.currentIndex() == 1:
            self.capture_timer.start(self.interval.currentData())

    def _view_changed(self):
        if self.reference is not None and not self.busy:
            self.activate(self.reference.recipe_name, self.views.currentData())

    def _display_changed(self):
        button = self.display_group.button(self.display_mode.currentIndex())
        if button is not None:
            button.setChecked(True)
        self.images.setCurrentIndex(self.display_mode.currentIndex())
        self.blink_timer.stop()
        if self.active and self.display_mode.currentIndex() == 1 and self.reference is not None:
            self._blink_current = False
            self._show_blink()
            if self.current is not None and self.current.shape[:2] == self.reference.raw_golden.shape[:2]:
                self.blink_timer.start()

    def _blink(self):
        self._blink_current = not self._blink_current
        self._show_blink()

    def _show_blink(self):
        current = self._blink_current and self.current is not None
        image = self.current if current else self.reference.raw_golden
        pixmap = _pixmap(image)
        # Identical image dimensions were checked before evaluation. Replacement
        # only changes pixels, preserving one shared scale, centre and viewport.
        if self.blink_view._pixmap_item is None or self.blink_view._pixmap_item.pixmap().size() != pixmap.size():
            self.blink_view.set_pixmap(pixmap)
        else:
            self.blink_view.update_display_pixmap(pixmap)
        self.blink_label.setText('AKTUÁLNA' if current else 'GOLDEN')
        self._refresh_arrows()
        self._border(self.blink_view, self._current_status() if current else 'ok')

    def hideEvent(self, event):
        self.detail_overlay.hide()
        self.deactivate()
        super().hideEvent(event)

"""Extensible diagnostic page with a read-only camera position module."""
import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QScrollArea, QStackedWidget, QFrame, QSizePolicy)
from app.ui.image_canvas import ImageView


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

        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        self.module = QComboBox()
        self.module.addItem('Overenie polohy kamery')
        layout.addWidget(self.module)
        self.recipe_label = QLabel('Aktívny recept: –')
        layout.addWidget(self.recipe_label)
        self.views = QComboBox()
        self.views.currentIndexChanged.connect(self._view_changed)
        layout.addWidget(self.views)
        self.limits_label = QLabel('Tolerancie receptu: –')
        self.limits_label.setWordWrap(True)
        layout.addWidget(self.limits_label)
        self.display_mode = QComboBox()
        self.display_mode.addItems(['VEDĽA SEBA', 'PREPÍNANIE'])
        self.display_mode.currentIndexChanged.connect(self._display_changed)
        layout.addWidget(self.display_mode)
        self.images = QStackedWidget()
        pair = QWidget()
        pair_layout = QHBoxLayout(pair)
        self.golden_view, self.current_view, self.blink_view = ImageView(), ImageView(), ImageView()
        for title, view in [('RAW GOLDEN', self.golden_view), ('RAW AKTUÁLNA', self.current_view)]:
            column = QWidget()
            col = QVBoxLayout(column)
            col.addWidget(QLabel(title))
            col.addWidget(view, 1)
            pair_layout.addWidget(column, 1)
        blink = QWidget()
        blink_layout = QVBoxLayout(blink)
        self.blink_label = QLabel('GOLDEN')
        blink_layout.addWidget(self.blink_label)
        blink_layout.addWidget(self.blink_view, 1)
        self.images.addWidget(pair)
        self.images.addWidget(blink)
        self.images.setMinimumHeight(180)
        self.images.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(self.images, 1)
        self.metrics = QLabel('ΔX – | ΔY – | Δrotation – | Confidence –')
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        self.status = QLabel('Zhotovte novú snímku.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.directions = QLabel()
        self.directions.setWordWrap(True)
        layout.addWidget(self.directions)
        note = QLabel('Pokyny platia pre pevnú formu a pohyb kamery v jej obrazovej rovine '
                      '(pohľad zozadu smerom na formu). ΔX/ΔY sú v súradniciach receptu. '
                      'Diagnostika nemení recept ani výsledky RUN.')
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QHBoxLayout()
        self.capture_mode = QComboBox()
        self.capture_mode.addItems(['Manuálne', 'Automaticky'])
        self.capture_mode.currentIndexChanged.connect(self._automatic_changed)
        controls.addWidget(self.capture_mode)
        self.interval = QComboBox()
        for text, ms in [('0.5 s', 500), ('1 s', 1000), ('2 s', 2000), ('5 s', 5000)]:
            self.interval.addItem(text, ms)
        self.interval.setCurrentIndex(1)
        controls.addWidget(self.interval)
        layout.addLayout(controls)
        self.capture_button = QPushButton('ZHOTOVIŤ SNÍMKU')
        self.capture_button.clicked.connect(self.capture)
        layout.addWidget(self.capture_button)
        self._border(self.golden_view, 'ok')
        self._controls()

    @staticmethod
    def _border(view, status):
        color = {'ok': '#30b66b', 'nok': '#e35353', 'idle': '#657080'}[status]
        view.setFrameShape(QFrame.Box)
        view.setStyleSheet(f'border: 3px solid {color};')

    def _controls(self):
        ready = self.active and not self.busy and self.reference is not None
        self.capture_button.setEnabled(ready)
        self.views.setEnabled(self.active and not self.busy)

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
        self.capture_timer.stop()
        self.blink_timer.stop()
        return self._submit('diagnostic_reference', lambda: self.service.open_reference(recipe_name, view_id, self.hardware_mode))

    def deactivate(self):
        was_active = self.active
        self.active = False
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
        self.directions.setText('\n'.join(result.corrections))
        self._border(self.current_view, self._current_status())

    def _current_status(self):
        return 'idle' if self.result is None or self.result.overall_ok is None else ('ok' if self.result.overall_ok else 'nok')

    def _automatic_changed(self):
        self.capture_timer.stop()
        self._schedule_capture()

    def _schedule_capture(self):
        if self.active and not self.busy and self.reference is not None and self.capture_mode.currentIndex() == 1:
            self.capture_timer.start(self.interval.currentData())

    def _view_changed(self):
        if self.reference is not None and not self.busy:
            self.activate(self.reference.recipe_name, self.views.currentData())

    def _display_changed(self):
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
        self._border(self.blink_view, self._current_status() if current else 'ok')

    def hideEvent(self, event):
        self.deactivate()
        super().hideEvent(event)

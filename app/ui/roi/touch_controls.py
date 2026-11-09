"""Compact context-sensitive controls for the existing shared ROI canvas."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QComboBox, QLabel
from app.ui.image_canvas import InteractionMode
from app.ui.run_options_overlay import RunOptionsOverlay


class TouchROIControls(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor, self.view = editor, editor._view
        self._syncing = False
        # Keep legacy controls alive for the editor's existing state management.
        editor._navigation.hide()
        editor._geometry_controls.hide()
        editor._geometry_controls.setMaximumHeight(0)
        editor._navigation.setMaximumHeight(0)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.context = QComboBox()
        self.context.setAccessibleName("Upravovaná oblasť")
        self.context.setMaximumWidth(180)
        row.addWidget(self.context)
        self.area_buttons = {}
        for text, area in (("Hľadanie", "search"), ("Šablóna", "template")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, target=area: self._select_area(target))
            row.addWidget(button)
            self.area_buttons[area] = button
        self.tool = QComboBox()
        self.tool.setAccessibleName("Kresliaci nástroj")
        self.tool.setMaximumWidth(130)
        row.addWidget(self.tool)
        self.select = QPushButton("Vybrať")
        self.pan = QPushButton("Posun")
        for mode, button in ((InteractionMode.SELECT, self.select), (InteractionMode.PAN, self.pan)):
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, m=mode: self.view.set_interaction_mode(m))
            row.addWidget(button)
        self.mask_options = QPushButton("Maska ▴")
        self.mask_options.clicked.connect(self._open_mask_options)
        row.addWidget(self.mask_options)
        self.refine = QPushButton("Spresniť hranu")
        self.refine.clicked.connect(editor._btn_edge_refine.click if hasattr(editor, '_btn_edge_refine') else lambda: None)
        row.addWidget(self.refine)
        row.addStretch()
        for button in (editor._btn_undo, editor._btn_redo):
            row.addWidget(button); button.show()
        editor.layout().insertWidget(0, self)
        navigation = QWidget()
        nav = QHBoxLayout(navigation)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(5)
        nav.addWidget(editor._btn_reset)
        editor._btn_reset.show()
        nav.addStretch()
        for button in (editor._navigation.zoom_out_button, editor._navigation.zoom_label,
                       editor._navigation.zoom_in_button, editor._navigation.fit_button):
            nav.addWidget(button); button.show()
        editor._navigation.fit_button.setText("Celý obraz")
        editor.layout().insertWidget(1, navigation)
        # Read-only geometry stays under the image; no numeric dialog is needed.
        bottom = QWidget()
        footer = QHBoxLayout(bottom)
        footer.setContentsMargins(0, 0, 0, 0)
        editor._info_label.setWordWrap(True)
        footer.addWidget(editor._info_label, 1)
        footer.addWidget(editor._btn_lock)
        editor.layout().addWidget(bottom)
        editor.layout().addWidget(editor._hint_label)
        editor._hint_label.setWordWrap(True)
        self.polygon_bar = QWidget()
        poly = QHBoxLayout(self.polygon_bar)
        poly.setContentsMargins(0, 0, 0, 0)
        self.finish = QPushButton("Dokončiť polygón")
        self.finish.setProperty("role", "primary")
        self.back = QPushButton("Späť o bod")
        self.cancel = QPushButton("Zrušiť")
        self.count = QLabel()
        for control in (self.finish, self.back, self.cancel, self.count): poly.addWidget(control)
        poly.addStretch()
        editor.layout().insertWidget(2, self.polygon_bar)
        self.finish.clicked.connect(lambda: self.view.finish_polygon())
        self.back.clicked.connect(lambda: self.view.remove_polygon_point())
        self.cancel.clicked.connect(lambda: self.view.cancel_drawing())
        for button in (self.finish, self.back, self.cancel):
            button.clicked.connect(self.sync)
        self.context.currentIndexChanged.connect(self._context_changed)
        self.tool.activated.connect(self._tool_changed)
        self.view.interactionModeChanged.connect(lambda *_: self.sync())
        self.view.historyChanged.connect(self.sync)
        self.view.roiChanged.connect(lambda *_: self.sync())
        if hasattr(self.view, "maskHistoryChanged"):
            self.view.maskHistoryChanged.connect(self.sync)
            self.view.edgeHistoryChanged.connect(self.sync)
        # Draft vertices do not emit committed roiChanged; refresh only on input.
        self.view.viewport().installEventFilter(self)
        self.view.zoomChanged.connect(lambda *_: self._rerender_handles())
        self.sync()

    def _rerender_handles(self):
        self.view._render_shape()
        if hasattr(self.view, '_render_edge_anchors'): self.view._render_edge_anchors()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.MouseButtonRelease, QEvent.TouchEnd, QEvent.KeyRelease):
            QTimer.singleShot(0, self, self.sync)
        return False

    def _context_changed(self):
        if self._syncing: return
        target = self.context.currentData()
        e = self.editor
        if hasattr(e, 'set_edit_context'):
            e.set_edit_context(target if target in ('mask', 'edge') else 'roi')
            if target in ('search', 'template'): e.select_locator_roi(target)
        self.sync()

    def _select_area(self, target):
        self.editor.set_edit_context('roi')
        self.editor.select_locator_roi(target)
        self.view.setFocus(Qt.OtherFocusReason)
        self.sync()

    def _tool_changed(self):
        if self.context.currentData() == 'mask':
            self.editor.set_mask_tool(self.tool.currentData())
        else:
            self.editor._start_shape_draw(self.tool.currentData())
        self.view.setFocus(Qt.OtherFocusReason)
        self.sync()

    def _open_mask_options(self):
        if not hasattr(self, 'overlay'):
            e = self.editor
            self.overlay = RunOptionsOverlay(self.view, 'Ignore mask', title='Ignore mask', scrollable=False)
            self.overlay.setObjectName('goldenOverlay')
            for label, widget in (("Veľkosť štetca", e._mask_brush_size),
                                   ("Priehľadnosť", e._mask_opacity), ("", e._btn_mask_visible)):
                if label: self.overlay.controls.addWidget(QLabel(label))
                widget.setMaximumWidth(16777215)
                self.overlay.controls.addWidget(widget)
                widget.show()
        self.overlay.open(self.mask_options)

    def sync(self):
        if self._syncing: return
        self._syncing = True
        try:
            e, v = self.editor, self.view
            context = e._active_edit_context()
            choices = [('ROI', 'roi')]
            locator = getattr(e, '_locator_mode', False)
            for area, button in self.area_buttons.items():
                button.setVisible(locator and context == 'roi')
                button.setEnabled(area != 'template' or not getattr(e, '_use_golden_crop', False))
                button.setChecked(locator and context == 'roi' and e._active_area == area)
            if getattr(e, '_mask_available', False): choices.append(('Ignore mask', 'mask'))
            if getattr(e, '_edge_available', False): choices.append((e._btn_edge_mode.text(), 'edge'))
            self.context.blockSignals(True)
            self.context.clear()
            for label, key in choices: self.context.addItem(label, key)
            self.context.setCurrentIndex(max(0, self.context.findData(context)))
            self.context.blockSignals(False)
            masking, edging = context == 'mask', context == 'edge'
            self.tool.blockSignals(True)
            self.tool.clear()
            tools = [('Obdĺžnik', 'rect'), ('Kruh / elipsa', 'ellipse'), ('Polygón', 'polygon')]
            if masking: tools = [('Štetec', 'brush'), ('Guma', 'eraser'), ('Obdĺžnik', 'rectangle'), ('Kruh', 'circle'), ('Polygón', 'polygon')]
            for label, key in tools: self.tool.addItem(label, key)
            selected = v._mask_mode if masking else v._draw_shape
            self.tool.setCurrentIndex(max(0, self.tool.findData(selected)))
            self.tool.blockSignals(False)
            self.tool.setVisible(not edging)
            self.tool.setEnabled(not v.is_roi_locked() or masking)
            self.mask_options.setVisible(masking)
            self.refine.setVisible(edging)
            if edging:
                a, b = v.edge_points()
                self.refine.setEnabled(a is not None and b is not None)
            self.select.setChecked(v.interaction_mode() == InteractionMode.SELECT)
            self.pan.setChecked(v.interaction_mode() == InteractionMode.PAN)
            polygon = v.interaction_mode() == InteractionMode.DRAW and (v._mask_mode == 'polygon' if masking else v._draw_shape == 'polygon') and not edging
            self.polygon_bar.setVisible(polygon)
            points = v._mask_polygon if masking else v._draft_points
            self.finish.setEnabled(len(points) >= 3)
            self.back.setEnabled(bool(points))
            self.count.setText(f'{len(points)} bodov')
            # Existing context methods may explicitly show the original bars.
            e._navigation.hide(); e._geometry_controls.hide()
            if hasattr(e, '_mask_controls'): e._mask_controls.hide()
        finally:
            self._syncing = False

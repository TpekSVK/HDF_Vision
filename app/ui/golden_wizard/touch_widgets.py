"""Touch presentation for Golden Wizard; recipe editing remains in its controllers."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QStackedWidget, QSizePolicy)


class ToolCard(QPushButton):
    activated = Signal()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ToolPages(QWidget):
    selected = Signal(int)
    activated = Signal(int)
    PAGE_SIZE = 5

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.table = table
        self.page = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.buttons = []
        for offset in range(self.PAGE_SIZE):
            button = ToolCard()
            button.setProperty("role", "toolCard")
            button.setMinimumHeight(44)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=offset: self.selected.emit(self.page * self.PAGE_SIZE + i))
            button.activated.connect(lambda i=offset: self.activated.emit(self.page * self.PAGE_SIZE + i))
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()
        nav = QHBoxLayout()
        self.previous = QPushButton("←")
        self.previous.setAccessibleName("Predošlé nástroje")
        self.next = QPushButton("→")
        self.next.setAccessibleName("Ďalšie nástroje")
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.previous.clicked.connect(lambda: self.move(-1))
        self.next.clicked.connect(lambda: self.move(1))
        nav.addWidget(self.previous)
        nav.addWidget(self.label, 1)
        nav.addWidget(self.next)
        layout.addLayout(nav)
        table.destroyed.connect(self._detach_table)
        table.model().modelReset.connect(self.refresh)
        table.model().rowsInserted.connect(lambda *_: QTimer.singleShot(0, self, self.refresh))
        table.model().rowsRemoved.connect(lambda *_: QTimer.singleShot(0, self, self.refresh))
        table.itemSelectionChanged.connect(self.follow_selection)

    def _detach_table(self):
        self.table = None

    def follow_selection(self):
        if self.table is None: return
        row = self.table.currentRow()
        if row >= 0:
            self.page = row // self.PAGE_SIZE
        self.refresh()

    def move(self, delta):
        self.page += delta
        self.refresh()

    def refresh(self):
        if self.table is None: return
        count = self.table.rowCount()
        pages = max(1, (count + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(pages - 1, self.page))
        for i, button in enumerate(self.buttons):
            row = self.page * self.PAGE_SIZE + i
            item = self.table.item(row, 1)
            button.setVisible(row < count)
            if item:
                name = item.text()
                # Let Qt wrap a long tool name without increasing the panel width.
                words, lines, line = name.split(), [], ""
                for word in words:
                    if len(line + " " + word) > 28 and line:
                        lines.append(line); line = ""
                    line = (line + " " + word).strip()
                lines.append(line)
                if len(lines) > 2:
                    lines = [lines[0], " ".join(lines[1:])[:26] + "…"]
                button.setText(f"{row + 1} · " + "\n".join(lines))
                button.setToolTip(name)
                button.setChecked(row == self.table.currentRow())
        self.label.setText(f"{self.page + 1} / {pages}")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)


class CompactToolResult(QWidget):
    """One result overview; optional previews/timings open above the image."""

    def __init__(self, panel, wizard):
        super().__init__()
        from PySide6.QtWidgets import QHeaderView
        from app.ui.run_options_overlay import RunOptionsOverlay
        self.panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QHBoxLayout()
        summary.addWidget(panel._status_indicator)
        summary.addWidget(panel._status_value_label)
        summary.addStretch()
        summary.addWidget(panel._latency_label)
        layout.addLayout(summary)
        layout.addWidget(panel._status_message_label)
        layout.addWidget(panel._metrics_table, 1)
        panel._metrics_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel._metrics_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        panel._metrics_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        panel._status_message_label.setStyleSheet('color: #aab2bc;')
        panel._status_message_label.setAlignment(Qt.AlignTop)
        self.previews = QPushButton('Náhľady ▴')
        self.timings = QPushButton('Časovanie ▴')
        actions = QHBoxLayout()
        actions.addWidget(self.previews)
        actions.addWidget(self.timings)
        layout.addLayout(actions)
        self.preview_overlay = RunOptionsOverlay(wizard._canvas_panel, 'Výsledok', title='Náhľady testu', scrollable=False)
        self.preview_overlay.setObjectName('goldenOverlay')
        self.preview_overlay.controls.addWidget(panel._preview_toggle_container)
        self.preview_overlay.controls.addWidget(panel._preview_widget)
        self.timing_overlay = RunOptionsOverlay(wizard._canvas_panel, 'Výsledok', title='Časovanie testu', scrollable=False)
        self.timing_overlay.setObjectName('goldenOverlay')
        self.timing_overlay.controls.addWidget(panel._perf_overlay_label)
        self.previews.clicked.connect(lambda: self.preview_overlay.open(self.previews))
        self.timings.clicked.connect(lambda: self.timing_overlay.open(self.timings))

    def refresh(self):
        self.previews.setEnabled(not self.panel._preview_widget.isHidden())
        self.timings.setEnabled(not self.panel._perf_overlay_label.isHidden())



class CompactToolLearning(QWidget):
    """Keep sample collection and model rebuilding together on one page."""

    def __init__(self, panel, wizard):
        super().__init__()
        from app.ui.run_options_overlay import RunOptionsOverlay
        self.panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(panel._presence_model_state)
        layout.addWidget(panel._presence_counts)
        layout.addWidget(panel._presence_warning)
        capture = QHBoxLayout()
        capture.setSpacing(6)
        for action in ('capture_ok', 'capture_nok'):
            capture.addWidget(panel._presence_capture_buttons[action])
            panel._presence_capture_labels[action].hide()
        layout.addLayout(capture)
        layout.addWidget(panel._shared_mold_ok)
        layout.addWidget(panel._presence_rebuild)
        self.details = QPushButton('Podrobnosti modelu ▴')
        layout.addWidget(self.details)
        layout.addStretch()
        self.overlay = RunOptionsOverlay(wizard._canvas_panel, 'Učenie', title='Podrobnosti modelu', scrollable=False)
        self.overlay.setObjectName('goldenOverlay')
        self.overlay.controls.addWidget(panel._presence_recommended)
        self.overlay.controls.addWidget(panel._presence_reset)
        self.details.clicked.connect(lambda: self.overlay.open(self.details))

    def refresh(self):
        self.panel._presence_warning.setVisible(bool(self.panel._presence_warning.text()))
        for action, button in self.panel._presence_capture_buttons.items():
            button.setToolTip(self.panel._presence_capture_labels[action].text())


class CompactToolValidation(QWidget):
    """Validation actions and decision summary together; full reports in an overlay."""

    def __init__(self, panel, wizard):
        super().__init__()
        from app.ui.run_options_overlay import RunOptionsOverlay
        self.panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        panel._presence_validate_button.setText('Otestovať\nvzorky')
        panel._presence_auto_settings_button.setText('Navrhnúť\nnastavenia')
        actions.addWidget(panel._presence_validate_button)
        actions.addWidget(panel._presence_auto_settings_button)
        layout.addLayout(actions)
        self.summary = QLabel('Zatiaľ bez výsledku')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet('color: #d29922;')
        layout.addWidget(self.warning)
        layout.addWidget(panel._presence_apply_tuned)
        self.details = QPushButton('Podrobnosti validácie ▴')
        layout.addWidget(self.details)
        layout.addStretch()
        self.overlay = RunOptionsOverlay(wizard._canvas_panel, 'Validácia', title='Podrobnosti validácie', scrollable=False)
        self.overlay.setObjectName('goldenOverlay')
        tabs = QHBoxLayout()
        self.validation_tab = QPushButton('Overenie vzoriek')
        self.tuning_tab = QPushButton('Návrh nastavení')
        for button in (self.validation_tab, self.tuning_tab):
            button.setCheckable(True)
            tabs.addWidget(button)
        self.overlay.controls.addLayout(tabs)
        self.reports = QStackedWidget()
        self.reports.addWidget(panel._presence_validation_result)
        self.reports.addWidget(panel._presence_tuning_result)
        self.overlay.controls.addWidget(self.reports)
        for label in (panel._presence_validation_result, panel._presence_tuning_result):
            label.setAlignment(Qt.AlignTop)
        self.validation_tab.clicked.connect(lambda: self._select_report(False))
        self.tuning_tab.clicked.connect(lambda: self._select_report(True))
        self.details.clicked.connect(self._open_details)
        panel.presenceValidationChanged.connect(self.refresh)
        self.refresh()

    def _select_report(self, tuning):
        self.reports.setCurrentIndex(1 if tuning else 0)
        self.validation_tab.setChecked(not tuning)
        self.tuning_tab.setChecked(tuning)
        # The existing presenter updates label visibility when a report arrives.
        # Explicitly keep the other stacked page hidden after those updates.
        self.panel._presence_validation_result.setVisible(not tuning)
        self.panel._presence_tuning_result.setVisible(tuning)
        report = self.reports.currentWidget()
        # Keep every result, but remove the legacy blank lines between each
        # metric. Size the overlay after the live report text has changed.
        report.setText(report.text().replace('\n\n', '\n'))
        width = max(1, min(520, self.overlay.parentWidget().width() - 24) - 24)
        self.reports.setMinimumHeight(max(120, report.minimumSizeHint().height(), report.heightForWidth(width)) + 8)
        self.overlay._content.layout().invalidate()
        if self.overlay.isVisible():
            self.overlay._place()

    def _open_details(self):
        self._select_report(bool(self.panel._compact_tuning_summary))
        self.overlay.open(self.details)

    def refresh(self):
        p = self.panel
        validation, tuning = p._compact_validation_summary, p._compact_tuning_summary
        warnings = []
        if tuning:
            before, after = tuning['current_validation_summary'], tuning['recommended_validation_summary']
            self.summary.setText(
                f"Navrhnutá citlivosť: {int(tuning['recommended_sensitivity'])} %\n"
                f"False Accept: {float(before['false_accept_rate'])*100:.1f} → {float(after['false_accept_rate'])*100:.1f} %\n"
                f"False Reject: {float(before['false_reject_rate'])*100:.1f} → {float(after['false_reject_rate'])*100:.1f} %")
            self.summary.setStyleSheet('color: #e6e8eb;')
            if not tuning['has_nok_samples']: warnings.append('⚠ Chýbajú NOK vzorky.')
            elif p._tuning_weak: warnings.append('⚠ Málo validačných vzoriek.')
            if tuning['has_nok_samples'] and (after['false_accept_count'] > 0 or float(after['false_reject_rate']) > .1):
                warnings.append('⚠ OK a NOK vzorky sa prekrývajú.')
        elif validation:
            good = validation['nok_total'] > 0 and validation['false_accept_count'] == 0 and float(validation['false_reject_rate']) <= .1
            quality = '● Dobré rozlíšenie' if good else '● Slabé rozlíšenie'
            accept = f"{float(validation['false_accept_rate'])*100:.1f} %" if validation['nok_total'] else 'bez NOK vzoriek'
            self.summary.setText(
                f"{quality}\nOK: {validation['ok_correct']} / {validation['ok_total']} · NOK: {validation['nok_correct']} / {validation['nok_total']}\n"
                f"False Reject: {float(validation['false_reject_rate'])*100:.1f} %\n"
                f"False Accept: {accept}\nÚspešnosť: {float(validation['accuracy'])*100:.1f} %")
            self.summary.setStyleSheet('color: #22c55e;' if good else 'color: #d29922;')
            if not validation['nok_total']: warnings.append('⚠ Chýbajú NOK vzorky.')
            if p._validation_weak: warnings.append('⚠ Málo validačných vzoriek.')
        else:
            self.summary.setText(p._presence_validation_result.text())
            self.summary.setStyleSheet('color: #aab2bc;')
        self.warning.setText('\n'.join(warnings))
        self.warning.setVisible(bool(warnings))
        p._presence_apply_tuned.setVisible(bool(tuning))
        self.tuning_tab.setEnabled(bool(tuning))
        self._select_report(bool(tuning))



class TouchInspector(QWidget):
    """Pages the existing live form widgets without recreating their values/signals."""

    def __init__(self, panel, wizard, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.page = 0
        self._key = None
        self._row_visibility = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Názov nástroja"))
        layout.addWidget(panel._name_input)
        self.groups = QComboBox()
        layout.addWidget(self.groups)
        self.body = QStackedWidget()
        self.body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout.addWidget(self.body, 1)
        self.sections = [
            ("Geometria", panel._geometry_section),
            ("Detekcia", panel._detection_section),
            ("Prahy", panel._threshold_section),
            ("Učenie", panel._presence_learning_section),
            ("Validácia", panel._presence_validation_section),
            ("Pokročilé", panel._advanced_section),
            ("Výsledok", panel._result_section),
        ]
        for _, section in self.sections:
            content = section._content
            section.layout().removeWidget(content)
            self.body.addWidget(content)
            section.hide()
        self.body.removeWidget(panel._diagnostics_group)
        panel._diagnostics_group.setParent(panel)
        panel._diagnostics_group.hide()
        self.result_page = CompactToolResult(panel, wizard)
        self.body.addWidget(self.result_page)
        learning_content = panel._presence_learning_section._content
        self.body.removeWidget(learning_content)
        learning_content.setParent(panel)
        learning_content.hide()
        self.learning_page = CompactToolLearning(panel, wizard)
        self.body.addWidget(self.learning_page)
        validation_content = panel._presence_validation_section._content
        self.body.removeWidget(validation_content)
        validation_content.setParent(panel)
        validation_content.hide()
        self.validation_page = CompactToolValidation(panel, wizard)
        self.body.addWidget(self.validation_page)
        self.model_button = panel._empty_mold_v2_button
        layout.addWidget(self.model_button)
        nav = QHBoxLayout()
        self.previous = QPushButton("←")
        self.previous.setAccessibleName("Predošlá stránka parametrov")
        self.next = QPushButton("→")
        self.next.setAccessibleName("Ďalšia stránka parametrov")
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignCenter)
        nav.addWidget(self.previous); nav.addWidget(self.page_label, 1); nav.addWidget(self.next)
        layout.addLayout(nav)
        layout.addWidget(panel._btn_defaults)
        self.groups.currentIndexChanged.connect(self._select_group)
        self.previous.clicked.connect(lambda: self._move(-1))
        self.next.clicked.connect(lambda: self._move(1))
        for signal in (panel.paramChanged, panel.thresholdChanged, panel.testButtonEnabledChanged):
            signal.connect(lambda *_: QTimer.singleShot(0, self, self.refresh))
        panel.hide()

    def prepare_tool_change(self):
        self._restore_rows()

    def refresh(self):
        p = self.panel
        tool = p._current_tool
        key = id(tool)
        changed = key != self._key
        self._key = key
        wanted = self.groups.currentData()
        if changed:
            self._row_visibility.clear()
            self.page = 0
        available = []
        if tool:
            available.append(0)
            for index, form in ((1, p._form_layout), (2, p._threshold_layout), (5, p._advanced_layout)):
                # Detection starts with a heading; that alone is not a setting.
                if form.rowCount() > (1 if index == 1 else 0):
                    available.append(index)
            if tool.type in {"presence.absence_v2", "mold.protection_v1"}:
                available.extend([3, 4])
            available.append(6)
        available.sort()
        self.groups.blockSignals(True)
        self.groups.clear()
        for index in available:
            self.groups.addItem(self.sections[index][1]._header.text(), index)
        target = self.groups.findData(wanted)
        self.groups.setCurrentIndex(target if target >= 0 else 0)
        self.groups.blockSignals(False)
        self.model_button.setVisible(bool(tool and tool.type == "mold.protection_v2"))
        for _, section in self.sections:
            section.hide()  # legacy set_tool may show the collapsed containers again
        if available:
            self._show_page()
        else:
            self.body.hide()
        self.groups.setEnabled(bool(tool))

    def _select_group(self, *_):
        self._restore_rows()
        self.page = 0
        self._show_page()

    def _move(self, delta):
        self.page += delta
        self._show_page()

    def _restore_rows(self):
        for widget, visible in list(self._row_visibility.items()):
            try: widget.setVisible(visible)
            except RuntimeError: pass  # previous tool's dynamically rebuilt form
        self._row_visibility.clear()

    def _show_mold_advanced(self):
        p = self.panel
        groups = [
            ('Zber', ['capture_mode_default', 'min_ok_samples', 'recommended_ok_samples', 'use_nok_for_validation']),
            ('Model', ['expected_state', 'reference_mode', 'model_method']),
        ]
        self.page = max(0, min(len(groups) - 1, self.page))
        title, names = groups[self.page]
        visible = {p._param_wrappers[name] for name in names}
        for widget in p._advanced_container.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            self._row_visibility[widget] = not widget.isHidden()
            widget.setVisible(widget in visible)
        checkbox, label = p._param_widgets['use_nok_for_validation'], p._param_labels['use_nok_for_validation']
        checkbox.setText(label.text())
        checkbox.setMinimumHeight(44)
        label.hide()
        self.page_label.setText(f'{self.page + 1} / 2 · {title}')
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page == 0)

    def _show_mold_sensitivity(self):
        p = self.panel
        groups = [
            ('Základné', ['sensitivity', 'polarity', 'min_blob_area']),
            ('Limity', ['max_largest_blob_area', 'max_blob_count', 'total_area_threshold']),
        ]
        self.page = max(0, min(len(groups) - 1, self.page))
        title, names = groups[self.page]
        wrappers = {**p._threshold_wrappers, 'polarity': p._param_wrappers['polarity']}
        visible = {wrappers[name] for name in names}
        visible.add(p._presence_live_values)
        for widget in p._threshold_container.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            self._row_visibility[widget] = not widget.isHidden()
            widget.setVisible(widget in visible)
        self.page_label.setText(f'{self.page + 1} / 2 · {title}')
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page == 0)

    def _show_locator_alignment(self):
        p = self.panel
        mode = p._param_widgets['alignment_mode'].currentData()
        groups = [('Režim', ['use_golden_crop', 'alignment_mode'])]
        if mode == 'guided_edge':
            groups = [
                ('Režim', ['use_golden_crop', 'alignment_mode', 'reference_max_angle_deg']),
                ('Hľadanie hrany', ['reference_search_half_window', 'reference_edge_polarity', 'reference_grad_threshold']),
                ('Presnosť hrany', ['reference_blur_sigma', 'reference_scan_step', 'reference_min_coverage', 'reference_use_subpixel']),
            ]
        self.page = max(0, min(len(groups) - 1, self.page))
        _, names = groups[self.page]
        visible = {p._param_wrappers[name] for name in names}
        for widget in p._form_container.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            self._row_visibility[widget] = not widget.isHidden()
            widget.setVisible(widget in visible)
        # Short labels keep related controls together at the inspector width;
        # their existing tooltips retain the complete descriptions and units.
        labels = {
            'reference_search_half_window': 'Okolie hrany na každú stranu',
            'reference_blur_sigma': 'Vyhladenie (σ)',
            'reference_scan_step': 'Rozostup meracích bodov',
            'reference_edge_polarity': 'Prechod hrany',
            'reference_grad_threshold': 'Minimálna sila hrany',
            'reference_min_coverage': 'Minimálne pokrytie',
            'reference_use_subpixel': 'Subpixelová presnosť',
        }
        for name, text in labels.items():
            p._param_labels[name].setText(text)
        for name in ('use_golden_crop', 'reference_use_subpixel'):
            checkbox, label = p._param_widgets[name], p._param_labels[name]
            checkbox.setText(label.text())
            checkbox.setMinimumHeight(44)
            label.hide()
        pages = len(groups)
        caption = f'{self.page + 1} / {pages}'
        if pages > 1:
            caption += ' · ' + groups[self.page][0]
        self.page_label.setText(caption)
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)

    def _show_page(self):
        self._restore_rows()
        index = self.groups.currentData()
        if index is None: return
        if index in (3, 4, 6):
            self.body.show()
            presentation = {3: self.learning_page, 4: self.validation_page, 6: self.result_page}[index]
            self.body.setCurrentWidget(presentation)
            presentation.refresh()
            self.page = 0
            self.page_label.setText("1 / 1")
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            return
        content = self.sections[index][1]._content
        self.body.show()
        self.body.setCurrentWidget(content)
        content.show()
        p = self.panel
        # Visibility of guided-edge fields is semantic, not a page choice.
        p._update_locator_mode_visibility()
        if index == 5 and p._current_tool is not None and p._current_tool.type == 'mold.protection_v1':
            self._show_mold_advanced()
            return
        if index == 2 and p._current_tool is not None and p._current_tool.type == 'mold.protection_v1':
            self._show_mold_sensitivity()
            return
        if index == 1 and p._current_tool is not None and p._current_tool.type == 'locator.template_match':
            self._show_locator_alignment()
            return
        # Use the content widgets directly. Keeping temporary QWidgetItem
        # wrappers across dynamic form rebuilds is unsafe in PySide 6.7.
        rows = [[widget] for widget in content.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                if not widget.isHidden()]
        # Keep a field and its label together (form uses stacked spanning rows).
        locator_acceptance = (index == 2 and p._current_tool is not None
                              and p._current_tool.type == 'locator.template_match')
        per_page = 3 if locator_acceptance else 2
        if locator_acceptance:
            for widget in p._threshold_widgets.values():
                widget.setMinimumHeight(max(44, widget.minimumHeight()))
        pages = max(1, (len(rows) + per_page - 1) // per_page)
        self.page = max(0, min(pages - 1, self.page))
        for i, widgets in enumerate(rows):
            for widget in widgets:
                self._row_visibility[widget] = True
                widget.setVisible(i // per_page == self.page)
        self.page_label.setText(f"{self.page + 1} / {pages}")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        # Avoid huge minimum sizes inherited from diagnostics previews.
        p._preview_before_label.setMinimumSize(100, 100)
        p._preview_after_label.setMinimumSize(100, 100)


def build_touch_workspace(wizard, capture, load):
    from PySide6.QtWidgets import QFrame, QGridLayout
    from app.ui.run_options_overlay import RunOptionsOverlay
    w = wizard
    root = QVBoxLayout(w)
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)
    top = QFrame()
    top.setObjectName("goldenTopBar")
    header = QHBoxLayout(top)
    header.setContentsMargins(8, 6, 8, 6)
    title = QLabel("GOLDEN WIZARD")
    title.setProperty("role", "wizardTitle")
    header.addWidget(title)
    header.addStretch()
    w._view_selector.setMinimumWidth(110)
    w._view_selector.setMaximumWidth(190)
    w.btn_add_view.setText("+ Pohľad")
    w.btn_live.setText("Live")
    w.btn_manual_light.setText("Svetlo")
    for widget in (w._view_selector, w.btn_add_view, w.btn_edit_view, w.btn_live, w.btn_manual_light):
        header.addWidget(widget)
    w._more_button = QPushButton("Ďalšie")
    w._more_button.setCheckable(True)
    header.addWidget(w._more_button)
    root.addWidget(top)

    workspace = QHBoxLayout()
    center = QFrame()
    center.setObjectName("workspacePanel")
    canvas = QVBoxLayout(center)
    canvas.setContentsMargins(8, 8, 8, 8)
    canvas.setSpacing(6)
    w._canvas_title = QLabel("GOLDEN")
    w._canvas_title.hide()
    w._canvas_empty = QLabel("Nie je dostupný GOLDEN obraz · Získajte ho alebo načítajte")
    w._canvas_empty.setAlignment(Qt.AlignCenter)
    w._canvas_empty.setWordWrap(True)
    w._canvas_empty.setAttribute(Qt.WA_TransparentForMouseEvents)
    canvas.addWidget(w._canvas_title)
    canvas.addWidget(w._canvas_empty)
    canvas.addWidget(w.live_lbl, 1)
    canvas.addWidget(w.roi_editor, 1)
    workspace.addWidget(center, 1)
    w._canvas_panel = center
    w.roi_editor.enable_touch_controls()

    right = QFrame()
    right.setObjectName("workspacePanel")
    right.setFixedWidth(320)
    side = QVBoxLayout(right)
    side.setContentsMargins(10, 10, 10, 10)
    tabs = QHBoxLayout()
    w._tools_tab = QPushButton("Nástroje")
    w._parameters_tab = QPushButton("Parametre")
    for b in (w._tools_tab, w._parameters_tab):
        b.setCheckable(True); tabs.addWidget(b)
    side.addLayout(tabs)
    w._touch_stack = QStackedWidget()
    w._touch_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    tool_page = QWidget()
    tool_layout = QVBoxLayout(tool_page)
    tool_layout.setContentsMargins(0, 0, 0, 0)
    w._tool_pages = ToolPages(w.tools_table)
    tool_layout.addWidget(w._tool_pages, 1)
    tool_layout.addWidget(w.btn_add_tool)
    w.locator_hint_label.hide()
    tool_layout.addWidget(w.locator_policy_banner)
    w._touch_stack.addWidget(tool_page)
    w._touch_inspector = TouchInspector(w._tool_panel, w)
    w._touch_stack.addWidget(w._touch_inspector)
    side.addWidget(w._touch_stack, 1)
    w.tools_table.hide()
    w.tools_table.setParent(w)
    w._tool_pages.selected.connect(w._mark_touch_tool)
    w._tool_pages.activated.connect(w._select_touch_tool)
    w._tools_tab.clicked.connect(lambda: w._show_touch_panel(0))
    w._parameters_tab.clicked.connect(lambda: w._show_touch_panel(1))
    actions = QGridLayout()
    w._btn_copy_selected = QPushButton("Kopírovať")
    w._btn_copy_selected.clicked.connect(lambda: w._copy_tool(w.tools_table.currentRow()))
    w._btn_delete_selected = QPushButton("Odstrániť")
    w._btn_delete_selected.setProperty("role", "destructive")
    w._btn_delete_selected.clicked.connect(w._confirm_delete_touch_tool)
    w._btn_tool_up = QPushButton("↑ Vyššie")
    w._btn_tool_down = QPushButton("↓ Nižšie")
    w._btn_tool_up.clicked.connect(lambda: w._move_touch_tool(-1))
    w._btn_tool_down.clicked.connect(lambda: w._move_touch_tool(1))
    w._btn_tool_enabled = QPushButton("Povolený")
    w._btn_tool_enabled.setCheckable(True)
    w._btn_tool_enabled.clicked.connect(lambda checked: w._toggle_tool_enabled(w.tools_table.currentRow(), checked))
    for index, button in enumerate((w._btn_copy_selected, w._btn_delete_selected, w._btn_tool_up,
                                     w._btn_tool_down, w._btn_tool_enabled)):
        actions.addWidget(button, index // 2, index % 2)
    tool_layout.addLayout(actions)
    workspace.addWidget(right)
    root.addLayout(workspace, 1)
    w._show_touch_panel(0)

    footer = QHBoxLayout()
    w._golden_button = QPushButton("Získať GOLDEN ▴")
    w.btn_publish_recipe.setText("Publikovať recept")
    w.btn_close_wizard.setText("Zavrieť Wizard")
    for widget in (w._golden_button, w.btn_save_tool, w.btn_test_tool): footer.addWidget(widget)
    footer.addWidget(w._publish_state_label, 1)
    footer.addWidget(w.btn_publish_recipe)
    footer.addWidget(w.btn_close_wizard)
    root.addLayout(footer)
    w._status_bar = QLabel("Pripravené")
    w._status_bar.setWordWrap(True)
    w._status_bar.setProperty("role", "wizardStatus")
    root.addWidget(w._status_bar)
    w._options_overlay = RunOptionsOverlay(center, "Golden Wizard", title="Ďalšie možnosti", scrollable=False)
    w._options_overlay.setObjectName("goldenOverlay")
    w._options_overlay.controls.addWidget(QLabel("Recept"))
    w._options_overlay.controls.addWidget(w.recipe_name)
    for widget in (w.chk_pose, w.chk_logging, w.chk_filtered_roi, w.lbl_filtered_roi):
        w._options_overlay.controls.addWidget(widget)
    w._options_overlay.controls.addWidget(QLabel("Pri zlyhaní locatora"))
    w._options_overlay.controls.addWidget(w.failure_policy_combo)
    w._session_settings_button.setText("Nastavenia relácie")
    row = QHBoxLayout()
    row.addWidget(w._session_settings_button); row.addWidget(w.btn_remove_view)
    w._options_overlay.controls.addLayout(row)
    w._more_button.clicked.connect(lambda checked: w._options_overlay.open(w._more_button) if checked else w._options_overlay.hide())
    w._options_overlay.closed.connect(lambda: w._more_button.setChecked(False))
    w._capture_overlay = RunOptionsOverlay(center, "Golden", title="Referenčný obraz", scrollable=False)
    w._capture_overlay.setObjectName("goldenOverlay")
    capture.setText("Získať z kamery")
    load.setText("Načítať zo súboru")
    for button in (capture, load):
        w._capture_overlay.controls.addWidget(button)
        button.clicked.connect(w._capture_overlay.hide)
    w._golden_button.clicked.connect(lambda: w._capture_overlay.open(w._golden_button))
    w._tool_panel.setParent(w)
    w._tool_panel.hide()

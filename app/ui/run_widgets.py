"""Bounded RUN summaries and paginated views/details, without scrollbars."""
from math import ceil

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QSizePolicy, QVBoxLayout, QWidget)

from app.ui.run_options_overlay import RunOptionsOverlay
from app.ui.theme import refresh_style


class SummaryLabel(QLabel):
    """One display line; retain the entire message for the details overlay."""
    changed = Signal()

    def __init__(self, text='', parent=None):
        self.full_text = str(text)
        self._override = None
        super().__init__('', parent)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._render()

    def setText(self, text):
        self.full_text = str(text)
        self._render()
        self.changed.emit()

    def set_override(self, text):
        self._override = text
        self._render()

    def _render(self):
        text = self.full_text if self._override is None else self._override
        self.setToolTip(text)
        super().setText(self.fontMetrics().elidedText(text, Qt.ElideRight,
                                                    max(1, self.contentsRect().width())))
        self.setFixedHeight(self.fontMetrics().height() + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._render()


class NoticeState(QObject):
    """Recovery state included in the summary and complete result details."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text, self._visible = '', False

    def setText(self, text):
        self._text = str(text)
        self.changed.emit()

    def text(self):
        return self._text

    def setVisible(self, visible):
        self._visible = bool(visible)
        self.changed.emit()

    def isVisible(self):
        return self._visible

    def show(self):
        self.setVisible(True)

    def hide(self):
        self.setVisible(False)


class _ViewButton(QPushButton):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name, self.result = name, '—'
        self.setCheckable(True)
        self.setProperty('runView', True)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setToolTip(name)
        self._render()

    def _render(self):
        name = self.fontMetrics().elidedText(self.name, Qt.ElideRight, max(1, self.width()-20))
        self.setText(f'{name} · {self.result}')
        self.setAccessibleName(f'{self.name}: {self.result}')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()


class RunViewGrid(QWidget):
    PAGE_SIZE = 4

    def __init__(self, *, on_view_selected, parent=None):
        super().__init__(parent)
        self._on_view_selected = on_view_selected
        self._items, self._active, self._page = {}, None, 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowMinimumHeight(0, 48)
        self.grid.setRowMinimumHeight(1, 48)
        layout.addLayout(self.grid)
        self.pager = QWidget()
        row = QHBoxLayout(self.pager)
        row.setContentsMargins(0, 0, 0, 0)
        self.previous = QPushButton('←')
        self.previous.setAccessibleName('Predchádzajúce pohľady')
        self.next = QPushButton('→')
        self.next.setAccessibleName('Ďalšie pohľady')
        for button in (self.previous, self.next):
            button.setMinimumSize(44, 44)
        self.page_label = QLabel(); self.page_label.setAlignment(Qt.AlignCenter)
        self.previous.clicked.connect(lambda: self._change_page(-1))
        self.next.clicked.connect(lambda: self._change_page(1))
        row.addWidget(self.previous); row.addWidget(self.page_label, 1); row.addWidget(self.next)
        layout.addWidget(self.pager)
        self._render()

    def set_views(self, views, *, thumbnail_loader=None):
        for button in self._items.values():
            self.grid.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._items = {}
        for entry in views:
            view_id = str(entry.get('id', '') if isinstance(entry, dict) else entry.id)
            name = str(entry.get('name', view_id) if isinstance(entry, dict) else entry.name)
            if not view_id:
                continue
            button = _ViewButton(name, self)
            button.clicked.connect(lambda checked=False, vid=view_id: self._select(vid))
            self._items[view_id] = button
        self._page = 0
        self._render()

    def _select(self, view_id):
        self._on_view_selected(view_id)
        self._render()  # Preserve the old selection if the station rejected it.

    def _change_page(self, offset):
        self._page = max(0, min(self._page + offset, max(0, ceil(len(self._items)/4)-1)))
        self._render()

    def _render(self):
        while self.grid.count():
            self.grid.takeAt(0)
        for index, (vid, button) in enumerate(self._items.items()):
            visible = index // self.PAGE_SIZE == self._page
            button.setVisible(visible)
            button.setChecked(vid == self._active)
            if visible:
                slot = index % self.PAGE_SIZE
                self.grid.addWidget(button, slot // 2, slot % 2)
        pages = max(1, ceil(len(self._items)/self.PAGE_SIZE))
        self.pager.setVisible(pages > 1)
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page+1 < pages)
        self.page_label.setText(f'{self._page+1} / {pages}')

    def set_active(self, view_id):
        self._active = view_id
        if view_id in self._items:
            self._page = list(self._items).index(view_id) // self.PAGE_SIZE
        self._render()

    def set_status(self, view_id, status):
        button = self._items.get(view_id)
        if button is not None:
            button.result = str(status).upper() if status else '—'
            button.setProperty('status', str(status).lower() if status else 'idle')
            button._render()
            refresh_style(button)

    def set_thumbnail(self, view_id, pixmap):
        pass  # RUN uses named buttons; the image is shown in the main preview.

    def has_view(self, view_id):
        return view_id in self._items

    def view_ids(self):
        return list(self._items)


class RunResultsOverlay(RunOptionsOverlay):
    def __init__(self, image, station_name):
        super().__init__(image, station_name, title='Detail výsledku', scrollable=False)
        self._text, self._page, self._pages = '', 0, ['Žiadne dáta']
        self._rendering = False
        self.body = QLabel()
        self.body.setTextFormat(Qt.PlainText)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.body.installEventFilter(self)
        self.controls.addWidget(self.body, 1)
        row = QHBoxLayout()
        self.previous = QPushButton('← Späť')
        self.next = QPushButton('Ďalej →')
        self.page_label = QLabel(); self.page_label.setAlignment(Qt.AlignCenter)
        self.previous.clicked.connect(lambda: self._change_page(-1))
        self.next.clicked.connect(lambda: self._change_page(1))
        row.addWidget(self.previous); row.addWidget(self.page_label, 1); row.addWidget(self.next)
        self.controls.addLayout(row)
        self._rendering = False

    def set_content(self, view_name, headline, message, recovery, rows):
        title = f'Detail výsledku · {view_name}'
        self.title.setText('Detail výsledku')
        self.title.setToolTip(title)
        self.setAccessibleName(title)
        text = '\n'.join([view_name, f'Stav stanice: {headline}', message, recovery, '',
                          'Nástroje vybraného pohľadu:',
                          *[f'{name}: {value or "—"}' for name, value in rows]])
        if text != self._text:
            self._text, self._page = text, 0
            self._paginate()

    def _place(self):
        image = self.parentWidget()
        width = max(1, min(680, image.width()-24))
        height = max(1, min(420, image.height()-24))
        self.setGeometry(max(0, image.width()-width-12), max(0, image.height()-height-12), width, height)

    def _paginate(self):
        if self._rendering:
            return
        self._rendering = True
        try:
            width = max(1, self.body.contentsRect().width()-4)
            lines = []
            option = QTextOption(); option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            for paragraph in self._text.split('\n'):
                if not paragraph:
                    lines.append('')
                    continue
                layout = QTextLayout(paragraph, self.body.font())
                layout.setTextOption(option)
                layout.beginLayout()
                while True:
                    line = layout.createLine()
                    if not line.isValid():
                        break
                    line.setLineWidth(width)
                    encoded = paragraph.encode('utf-16-le')
                    start, end = line.textStart(), line.textStart()+line.textLength()
                    lines.append(encoded[start*2:end*2].decode('utf-16-le'))
                layout.endLayout()
            capacity = max(1, self.body.height() // self.body.fontMetrics().lineSpacing()-1)
            self._pages = ['\n'.join(lines[i:i+capacity]) for i in range(0, len(lines), capacity)] or ['Žiadne dáta']
            self._page = min(self._page, len(self._pages)-1)
            self._display()
        finally:
            self._rendering = False

    def _change_page(self, offset):
        self._page = max(0, min(self._page+offset, len(self._pages)-1))
        self._display()

    def _display(self):
        self.body.setText(self._pages[self._page])
        self.page_label.setText(f'{self._page+1} / {len(self._pages)}')
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page+1 < len(self._pages))

    def open(self, anchor):
        super().open(anchor)
        self._paginate()

    def eventFilter(self, watched, event):
        if hasattr(self, 'body') and watched is self.body and event.type() in (QEvent.Resize, QEvent.FontChange):
            self._paginate()
        return super().eventFilter(watched, event)

"""Display-only comparison of a frozen NOK frame and its golden reference."""
from PySide6.QtCore import QObject, QTimer, Signal


class NokComparison(QObject):
    switchingChanged = Signal(bool)

    def __init__(self, show_frame, show_caption, can_show, parent=None):
        super().__init__(parent)
        self.show_frame = show_frame
        self.show_caption = show_caption
        self.can_show = can_show
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.advance)
        self.golden = self.nok = None
        self.showing_golden = False

    @property
    def active(self):
        return self.nok is not None

    def start(self, golden, nok):
        self.stop()
        if golden is None or nok is None or golden.shape[:2] != nok.shape[:2]:
            return
        self.golden, self.nok = golden.copy(), nok.copy()
        self.showing_golden = False
        self.render()
        if self.active:
            self.timer.start()
            self.switchingChanged.emit(True)

    def render(self):
        if not self.active:
            return
        if not self.can_show():
            self.stop()
            return
        self.show_frame(self.golden if self.showing_golden else self.nok)
        self.show_caption('GOLDEN – referencia (posledná kontrola NOK)' if self.showing_golden
                          else 'NOK – chybná snímka')

    def advance(self):
        if not self.timer.isActive():
            return
        self.showing_golden = not self.showing_golden
        self.render()

    def hold_nok(self):
        if not self.active:
            return
        self.timer.stop()
        self.showing_golden = False
        self.render()
        self.switchingChanged.emit(False)

    def stop(self):
        self.timer.stop()
        self.golden = self.nok = None
        self.show_caption('')
        self.switchingChanged.emit(False)

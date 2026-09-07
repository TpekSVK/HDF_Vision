"""Shared HDF Vision visual system."""

COLOR_BG = "#14181d"
COLOR_PANEL = "#181d23"
COLOR_BORDER = "#2a313a"
COLOR_TEXT = "#e6e8eb"
COLOR_TEXT_MUTED = "#9aa4af"
COLOR_PRIMARY = "#2f80c9"
COLOR_OK = "#22c55e"
COLOR_WARN = "#d29922"
COLOR_NOK = "#ef4444"


DARK_STYLE = """
QWidget {
  background-color: #14181d;
  color: #e6e8eb;
  font-family: "Segoe UI", "Inter", "DejaVu Sans";
  font-size: 11pt;
}
QMainWindow, QWidget#mainWindowRoot { background: #14181d; }
QLabel { background: transparent; }
QFrame#appTopBar, QFrame[role="panel"], QWidget[role="panel"] {
  background: #181d23;
  border: 1px solid #2a313a;
  border-radius: 6px;
}
QFrame[role="card"], QWidget[role="card"] {
  background: #1c2229;
  border: 1px solid #2a313a;
  border-radius: 6px;
}
QLabel[role="appTitle"] { font-size: 17px; font-weight: 700; color: #f4f6f8; }
QLabel[role="panelHeader"] { color: #e6e8eb; font-size: 12px; font-weight: 700; }
QLabel[role="secondary"] { color: #9aa4af; font-size: 10pt; }
QLabel[role="metricValue"] { color: #e6e8eb; font-size: 18px; font-weight: 700; }
QLabel[role="statusHero"] { font-size: 36px; font-weight: 800; }
QLabel[role="resultName"] { color: #c8cdd3; font-size: 11pt; }
QLabel[role="resultValue"] { font-size: 11pt; font-weight: 700; }
QLabel[status="ok"] { color: #22c55e; }
QLabel[status="warn"] { color: #d29922; }
QLabel[status="nok"] { color: #ef4444; }
QLabel[status="idle"] { color: #9aa4af; }

QPushButton, QToolButton {
  min-height: 24px;
  background: #20262d;
  color: #e6e8eb;
  border: 1px solid #343c46;
  border-radius: 4px;
  padding: 5px 10px;
}
QPushButton:hover, QToolButton:hover { background: #29313a; border-color: #46515e; }
QPushButton:pressed, QToolButton:pressed { background: #151a20; }
QPushButton:checked, QToolButton:checked {
  background: #173f61; border-color: #2f80c9; color: #ffffff;
}
QPushButton:disabled, QToolButton:disabled {
  color: #6f7882; background: #1a1f25; border-color: #252c34;
}
QPushButton[role="primary"] {
  background: #2f80c9; border-color: #3b91dc; color: #ffffff; font-weight: 700;
}
QPushButton[role="primary"]:hover { background: #3b91dc; }
QPushButton[role="destructive"] {
  background: #712b2b; border-color: #8f3a3a; color: #f3dddd;
}
QPushButton[role="warning"] {
  background: #4b3215; border-color: #7a5521; color: #f1d6a3;
}
QPushButton[role="mode"] { min-width: 96px; font-weight: 700; }
QPushButton[role="mode"]:checked {
  background: #2f80c9; border-color: #3b91dc; color: white;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {
  min-height: 26px;
  background: #11161b;
  color: #e6e8eb;
  border: 1px solid #303842;
  border-radius: 4px;
  padding: 3px 7px;
  selection-background-color: #1f5f91;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateEdit:focus { border-color: #2f80c9; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
  background: #181d23; border: 1px solid #343c46;
  selection-background-color: #1f5f91; outline: none;
}
QCheckBox { spacing: 6px; color: #c6ccd3; }

QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: #14181d; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3a444f; min-height: 28px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #14181d; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #3a444f; min-width: 28px; border-radius: 5px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QTableWidget, QTableView, QTreeWidget, QListWidget {
  background: #15191e;
  alternate-background-color: #1a2026;
  border: 1px solid #2a313a;
  gridline-color: #252c34;
  selection-background-color: #173f61;
  outline: none;
}
QHeaderView::section {
  background: #1c2229; color: #9aa4af; border: none;
  border-bottom: 1px solid #2a313a; padding: 6px;
}
QTabWidget::pane { border: 1px solid #2a313a; background: #181d23; }
QTabBar::tab {
  background: #181d23; color: #9aa4af; border: 1px solid #2a313a; padding: 7px 12px;
}
QTabBar::tab:selected { color: white; border-bottom: 2px solid #2f80c9; }
QGroupBox {
  border: 1px solid #2a313a; border-radius: 6px;
  margin-top: 12px; padding-top: 7px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
QToolTip { background: #20262d; color: #e6e8eb; border: 1px solid #46515e; padding: 4px; }
QFrame[frameShape="4"] {
  color: #2a313a; background: #2a313a; min-height: 1px; max-height: 1px;
}

QWidget#runContainer { background: transparent; }
QLabel#liveInspectionView {
  background: #0d1115; border: 1px solid #343c46;
  border-radius: 6px; color: #6f7882;
}
QLabel#liveInspectionView[status="ok"] { border: 2px solid #22c55e; }
QLabel#liveInspectionView[status="warn"] { border: 2px solid #d29922; }
QLabel#liveInspectionView[status="nok"] { border: 2px solid #ef4444; }
QFrame#runStatusCard[status="ok"] { border: 1px solid #287a43; background: #17261d; }
QFrame#runStatusCard[status="warn"] { border: 1px solid #765d20; background: #282315; }
QFrame#runStatusCard[status="nok"] { border: 1px solid #8f3a3a; background: #2a181a; }
QFrame#runStatusCard[status="idle"] { border: 1px solid #2a313a; background: #1c2229; }
QFrame#viewStripItem {
  background: #1c2229; border: 1px solid #2a313a; border-radius: 5px;
}
QFrame#viewStripItem:hover { background: #202731; border-color: #3a4653; }
QFrame#viewStripItem[active="true"] {
  background: #173f61; border: 2px solid #2f80c9;
}
QLabel#viewThumbnail { background: #11161b; border: 1px solid #2a313a; border-radius: 3px; }
QLabel#viewStatus {
  background: #252c34; color: #9aa4af; border-radius: 9px; padding: 2px 7px;
}
QLabel#viewStatus[status="ok"] { background: #173d25; color: #22c55e; }
QLabel#viewStatus[status="warn"] { background: #3b3218; color: #d29922; }
QLabel#viewStatus[status="nok"] { background: #431d20; color: #ef4444; }
"""


def refresh_style(widget) -> None:
    """Re-evaluate dynamic Qt properties without replacing the global QSS."""

    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


__all__ = [
    "COLOR_BG", "COLOR_BORDER", "COLOR_NOK", "COLOR_OK", "COLOR_PANEL",
    "COLOR_PRIMARY", "COLOR_TEXT", "COLOR_TEXT_MUTED", "COLOR_WARN",
    "DARK_STYLE", "refresh_style",
]

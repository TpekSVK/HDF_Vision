"""Golden Wizard-specific presentation helpers."""

GOLDEN_WIZARD_STYLE = """
QDialog#goldenWizard { background: #14181d; color: #e6e8eb; }
QDialog#goldenWizard QWidget { color: #e6e8eb; background: transparent; }
QDialog#goldenWizard QFrame#goldenTopBar,
QDialog#goldenWizard QFrame#workspacePanel,
QDialog#goldenWizard QScrollArea#propertiesScroll {
    background: #181d23; border: 1px solid #2a313a;
}
QDialog#goldenWizard QFrame#goldenTopBar { border-width: 0 0 1px 0; }
QDialog#goldenWizard QLabel[role="panelHeader"] {
    color: #e6e8eb; font-weight: 700; font-size: 12px;
    border-bottom: 1px solid #2a313a; padding: 2px 0 7px 0;
}
QDialog#goldenWizard QPushButton,
QDialog#goldenWizard QToolButton {
    background: #20262d; color: #e6e8eb; border: 1px solid #343c46;
    border-radius: 4px; padding: 5px 9px;
}
QDialog#goldenWizard QPushButton:hover,
QDialog#goldenWizard QToolButton:hover {
    background: #29313a; border-color: #46515e;
}
QDialog#goldenWizard QPushButton:disabled,
QDialog#goldenWizard QToolButton:disabled { color: #6f7882; background: #1a1f25; }
QDialog#goldenWizard QPushButton[role="primary"] {
    background: #2f80c9; border-color: #3b91dc; color: white; font-weight: 600;
}
QDialog#goldenWizard QPushButton[role="primary"]:hover { background: #3b91dc; }
QDialog#goldenWizard QPushButton[role="destructive"] {
    background: #712b2b; border-color: #8f3a3a; color: #f3dddd;
}
QDialog#goldenWizard QToolButton#sectionHeader {
    background: transparent; border: none; border-bottom: 1px solid #2a313a;
    border-radius: 0; padding: 7px 2px; text-align: left; font-weight: 600;
}
QDialog#goldenWizard QToolButton#sectionHeader:hover { background: #20262d; }
QDialog#goldenWizard QLineEdit,
QDialog#goldenWizard QComboBox,
QDialog#goldenWizard QSpinBox,
QDialog#goldenWizard QDoubleSpinBox {
    background: #11161b; color: #e6e8eb; border: 1px solid #303842;
    border-radius: 4px; padding: 4px 6px;
}
QDialog#goldenWizard QLineEdit:focus,
QDialog#goldenWizard QComboBox:focus,
QDialog#goldenWizard QSpinBox:focus,
QDialog#goldenWizard QDoubleSpinBox:focus { border-color: #2f80c9; }
QDialog#goldenWizard QTableWidget {
    background: #15191e; alternate-background-color: #1a2026;
    border: 1px solid #2a313a; gridline-color: #252c34; selection-background-color: #1f5f91;
}
QDialog#goldenWizard QHeaderView::section {
    background: #1c2229; color: #9aa4af; border: none;
    border-bottom: 1px solid #2a313a; padding: 6px;
}
QDialog#goldenWizard QTableWidget::item:hover { background: #232b34; }
QDialog#goldenWizard QGroupBox { background: transparent; border: none; margin-top: 4px; }
QDialog#goldenWizard QScrollArea, QDialog#goldenWizard QScrollArea > QWidget > QWidget {
    background: #181d23;
}
QDialog#goldenWizard QSplitter::handle { background: #2a313a; }
"""


FIELD_LABELS_SK = {
    "use_golden_crop": "Použiť výrez z GOLDEN",
    "coarse_to_fine": "Hrubé → presné hľadanie",
    "coarse_cap": "Maximálna veľkosť hrubého hľadania",
    "angle_enabled": "Kompenzovať rotáciu",
    "angle_method": "Metóda uhla",
    "angle_ref_deg": "Referenčný uhol",
    "angle_max_dev_deg": "Max. odchýlka uhla",
    "angle_smooth": "Vyhladenie uhla",
    "angle_range_deg": "Rozsah hľadania uhla",
    "angle_step_deg": "Krok uhla",
    "apply_alignment": "Použiť zarovnanie",
}


def field_label(name: str, configured_label: object = None) -> str:
    return FIELD_LABELS_SK.get(name, str(configured_label or name).replace("_", " ").capitalize())

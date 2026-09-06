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
    "rotation_enabled": "Hľadať rotáciu",
    "angle_enabled": "Určiť výsledný uhol",
    "template_roi": "Oblasť šablóny",
    "angle_roi": "Oblasť uhla",
    "threshold_corr": "Minimálna zhoda",
}

METRIC_LABELS_SK = {
    "corr": "Skóre zhody",
    "score": "Skóre",
    "dx": "Posun X",
    "dy": "Posun Y",
    "x": "X",
    "y": "Y",
    "theta_deg": "Rotácia",
    "rotation": "Rotácia",
    "ssim": "SSIM",
    "similarity": "Podobnosť",
    "diff": "Rozdiel",
    "count": "Počet",
    "area": "Plocha",
    "found": "Nájdené",
    "match_attempts": "Pokusy o zhodu",
    "edge_count": "Počet hrán",
}


def field_label(name: str, configured_label: object = None) -> str:
    return FIELD_LABELS_SK.get(name, str(configured_label or name).replace("_", " ").capitalize())


def metric_label(name: str, configured_label: object = None) -> str:
    return METRIC_LABELS_SK.get(
        name, str(configured_label or name).replace("_", " ").capitalize()
    )


TOOL_CATALOG_STYLE = """
QDialog#toolCatalog { background: #14181d; color: #e6e8eb; }
QDialog#toolCatalog QWidget { color: #e6e8eb; background: transparent; }
QDialog#toolCatalog QFrame#catalogPanel {
    background: #181d23; border: 1px solid #2a313a; border-radius: 4px;
}
QDialog#toolCatalog QLabel[role="dialogTitle"] { font-size: 18px; font-weight: 700; }
QDialog#toolCatalog QLabel[role="panelHeader"] {
    color: #9aa4af; font-size: 11px; font-weight: 700;
    border-bottom: 1px solid #2a313a; padding-bottom: 7px;
}
QDialog#toolCatalog QLineEdit {
    min-width: 300px; background: #11161b; border: 1px solid #303842;
    border-radius: 4px; padding: 7px 9px;
}
QDialog#toolCatalog QLineEdit:focus { border-color: #2f80c9; }
QDialog#toolCatalog QListWidget { background: transparent; border: none; outline: none; }
QDialog#toolCatalog QListWidget#categoryList::item {
    border-radius: 4px; padding: 9px 10px; margin: 1px 0;
}
QDialog#toolCatalog QListWidget#categoryList::item:hover { background: #202731; }
QDialog#toolCatalog QListWidget#categoryList::item:selected {
    background: #173f61; color: white; border-left: 3px solid #2f80c9;
}
QDialog#toolCatalog QListWidget#toolCards::item { background: transparent; border: none; }
QDialog#toolCatalog QFrame#toolCard {
    background: #181d23; border: 1px solid #2a313a;
    border-radius: 6px; margin: 3px 5px;
}
QDialog#toolCatalog QFrame#toolCard:hover { background: #202731; border-color: #3a4653; }
QDialog#toolCatalog QFrame#toolCard[selected="true"] {
    background: #173f61; border-color: #2f80c9;
}
QDialog#toolCatalog QFrame#toolCard[deprecated="true"] { color: #6f7882; }
QDialog#toolCatalog QLabel[role="cardTitle"] { font-size: 14px; font-weight: 700; }
QDialog#toolCatalog QLabel[role="secondary"] { color: #9aa4af; }
QDialog#toolCatalog QLabel[role="technical"] { color: #6f7882; font-size: 10px; }
QDialog#toolCatalog QLabel[role="capabilities"] { color: #b8c2cc; font-size: 11px; }
QDialog#toolCatalog QLabel[role="warningBadge"] {
    color: #d29922; background: #2b2518; border: 1px solid #6f581d;
    border-radius: 3px; padding: 2px 5px;
}
QDialog#toolCatalog QLabel[role="emptyState"] { color: #6f7882; font-size: 13px; }
QDialog#toolCatalog QPushButton {
    background: #20262d; border: 1px solid #343c46; border-radius: 4px; padding: 7px 12px;
}
QDialog#toolCatalog QPushButton:hover { background: #29313a; border-color: #46515e; }
QDialog#toolCatalog QPushButton:disabled { color: #6f7882; background: #1a1f25; }
QDialog#toolCatalog QPushButton[role="primary"] {
    background: #2f80c9; border-color: #3b91dc; color: white; font-weight: 600;
}
QDialog#toolCatalog QSplitter::handle { background: #2a313a; }
"""

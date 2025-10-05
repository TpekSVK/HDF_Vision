# app/ui/theme.py
DARK_STYLE = """
QWidget {
  background-color: #1e1e1e;
  color: #ddd;
  font-family: 'Segoe UI', 'DejaVu Sans';
  font-size: 11pt;
}
QPushButton {
  background-color: #333;
  color: #eee;
  border: 1px solid #555;
  border-radius: 6px;
  padding: 6px 10px;
}
QPushButton:hover { background-color: #444; }
QPushButton:pressed { background-color: #555; }

QComboBox, QSpinBox {
  background-color: #2a2a2a;
  border: 1px solid #444;
  border-radius: 4px;
  padding: 2px 6px;
  color: #eee;
}

QScrollArea { background-color: #181818; border: none; }
QFrame[frameShape=\"4\"] { /* HLine */ color: #444; background: #444; min-height: 1px; max-height: 1px; }
"""

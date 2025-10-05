from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow
import sys
from app.ui.theme import DARK_STYLE

def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(900, 600)
    w.show()
    sys.exit(app.exec())
    app.setStyleSheet(DARK_STYLE)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("[FATAL] Unhandled exception:", e)
        traceback.print_exc()
        raise

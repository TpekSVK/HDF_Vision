from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow
import sys
from app.ui.theme import DARK_STYLE

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    w = MainWindow()
    w.resize(900, 600)
    w.showFullScreen()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        print("[FATAL] Unhandled exception:", e)
        traceback.print_exc()
        raise


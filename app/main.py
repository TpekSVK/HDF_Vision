from PySide6.QtWidgets import QApplication
from app.ui.workstation_window import WorkstationWindow
import sys
import os
from app.ui.theme import DARK_STYLE
from app.ui.system_keyboard import SystemKeyboard

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    app.system_keyboard = SystemKeyboard(app)
    w = WorkstationWindow()
    w.resize(900, 600)
    w.showFullScreen()
    ready_file = os.environ.get('HDF_STARTUP_READY_FILE')
    if ready_file:
        from app.ui.startup_ready import StartupReady
        w.startup_ready = StartupReady(w, ready_file)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        print("[FATAL] Unhandled exception:", e)
        traceback.print_exc()
        raise


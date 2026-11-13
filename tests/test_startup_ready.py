from types import SimpleNamespace
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from app.ui.startup_ready import StartupReady


def test_cover_waits_for_visible_window_and_completed_camera_preparation(tmp_path):
    app = QApplication.instance() or QApplication([])
    class Window(QObject):
        visible = False
        exposed = False
        workspaces = [SimpleNamespace(runtime_worker=SimpleNamespace(busy=True), _pending_runtime_action=None)]
        def isVisible(self): return self.visible
        def windowHandle(self): return SimpleNamespace(isExposed=lambda: self.exposed)
    window = Window()
    notifier = StartupReady(window, tmp_path / 'ready')
    notifier.check(); assert notifier.timer.isActive()
    window.visible = window.exposed = True
    notifier.check(); assert notifier.timer.isActive()
    window.workspaces[0].runtime_worker.busy = False
    window.workspaces[0]._pending_runtime_action = 'pause'
    notifier.check(); assert notifier.timer.isActive()
    window.workspaces[0]._pending_runtime_action = None
    notifier.check(); assert not notifier.timer.isActive()
    notifier.announce()
    assert (tmp_path / 'ready').read_text() == 'ready\n'
    notifier.deleteLater()


def test_installer_preserves_original_launchers_and_restores_them(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('setup_launcher', Path('tools/setup-launcher.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    desktop = tmp_path / 'Desktop/HDF_Vision.desktop'; desktop.parent.mkdir()
    original = b'[Desktop Entry]\nTerminal=true\nExec=sleep 8\n'
    desktop.write_bytes(original)
    checkout = tmp_path / 'project with spaces'
    module.configure(tmp_path, checkout)
    assert 'Terminal=false' in desktop.read_text()
    assert 'sleep 8' not in desktop.read_text()
    assert 'X-GNOME-Autostart-Notify=true' in (tmp_path / '.config/autostart/HDF_Vision.desktop').read_text()
    assert '"' + str(checkout / 'tools/launch-app.py') + '"' in desktop.read_text()
    module.configure(tmp_path, checkout)
    module.configure(tmp_path, checkout, restore=True)
    assert desktop.read_bytes() == original
    assert not (tmp_path / '.config/autostart/HDF_Vision.desktop').exists()

import threading
from PySide6.QtWidgets import QApplication

from app.ui.workcell_session import WorkcellSession
from test_workcell_runtime import workcell
from test_inspection_window_lifecycle import drain


def test_two_lanes_and_publication_keep_qt_responsive_without_queue(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    runtime, signals, _ = workcell(tmp_path, monkeypatch, mode='independent')
    session = WorkcellSession(runtime)
    entered, release = threading.Event(), threading.Event()
    ui = threading.get_ident()
    publisher_threads, displays = [], []
    def output(status, *, group_id):
        publisher_threads.append(threading.get_ident())
        if group_id == 'group_1':
            entered.set()
            assert release.wait(3)
        signals.append((status,group_id))
    runtime.modbus.signal_result=output
    session.result.connect(lambda result: displays.append(threading.get_ident()))
    try:
        assert session.request('modbus', {'input_index':1})
        drain(app, entered.is_set)
        assert runtime.processing
        assert not session.request('modbus', {'input_index':1})
        assert session.request('modbus', {'input_index':2})
        drain(app, lambda: ('ok','group_2') in signals)
        assert runtime.processing and all(ident != ui for ident in publisher_threads)
        release.set()
        drain(app, lambda: not runtime.processing and len(displays)==2)
        assert set(displays)=={ui}
    finally:
        release.set()
        drain(app, lambda: not runtime.processing)
        runtime.quiesce(); session.shutdown(); session.deleteLater()

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.ui.main_window import MainWindow


@pytest.mark.parametrize('mode', ['master', 'trigger'])
def test_wizard_prepares_session_before_light_and_refreshes_after_restore(monkeypatch, mode):
    events=[]
    pico=SimpleNamespace(session='IDLE',light=False)
    def prepare(**kwargs):
        assert kwargs == {'ensure_runtime_ready':True}
        pico.session=mode.upper();pico.light=False
        events.append('prepare')
        return True
    class Wizard:
        def __init__(self,*args,**kwargs):
            assert kwargs['pico'] is pico
            assert pico.session == mode.upper()
        def exec(self):
            pico.light=True
            events.append('wizard')
    monkeypatch.setattr('app.ui.main_window.GoldenWizard',Wizard)
    host=SimpleNamespace(capture_mode=mode,cam=object(),recipes=object(),modbus=None,pico=pico,
        get_capture_mode=lambda:mode,capture_frame_for_golden=Mock(),session_settings=None,
        _apply_capture_mode=prepare,_active_view_id='view_1',
        _refresh_manual_light=lambda:events.append(('light',pico.light)),
        _refresh_views=Mock(),_reload_results_strip=Mock(),_refresh_tool_selector=Mock(),_update_sidebar=Mock())
    MainWindow.open_wizard(host)
    assert events == ['prepare','wizard','prepare',('light',False)]


def test_wizard_not_opened_if_session_preparation_fails(monkeypatch):
    wizard=Mock()
    monkeypatch.setattr('app.ui.main_window.GoldenWizard',wizard)
    MainWindow.open_wizard(SimpleNamespace(_apply_capture_mode=lambda **kwargs:False))
    wizard.assert_not_called()

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('display_guard', Path(__file__).parents[1] / 'tools/boot_branding/display-guard.py')
guard = importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)


@pytest.mark.parametrize('pids,expected', [(['100'] * 12, True), ([str(i) for i in range(12)], False), ([''] * 12, False), (['100', '101', '102'] + ['103'] * 9, True)])
def test_a_restarting_xserver_is_not_mistaken_for_a_stable_desktop(monkeypatch, pids, expected):
    values = iter(pids)
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=next(values)))
    monkeypatch.setattr(guard.time, 'sleep', lambda *a: None)
    assert guard.stable_xserver() is expected


@pytest.fixture
def installed(tmp_path, monkeypatch):
    module = tmp_path / 'modules.conf';helper = tmp_path / 'helper';base = tmp_path / 'extlinux.conf'
    for p in (module,helper,base):p.write_text(p.name)
    state = tmp_path / 'state.json'
    meta = dict(additional_created=[str(helper),str(module)], display_handoff='trial',
        destinations={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (module,helper,base)})
    state.write_text(json.dumps(meta))
    monkeypatch.setattr(guard, 'STATE', state)
    monkeypatch.setattr(guard, 'ALLOWED', {str(module),str(helper)})
    calls=[]
    monkeypatch.setattr(guard, 'command', lambda *a, **kw:calls.append(a))
    return meta,module,helper,base,calls


def test_recovery_removes_only_trial_files_and_starts_gdm_after_cleanup(installed):
    meta,module,helper,base,calls=installed
    before=base.read_bytes();guard.recover(meta)
    assert not module.exists() and not helper.exists()
    assert base.read_bytes()==before
    assert list(json.loads(guard.STATE.read_text())['destinations'])==[str(base)]
    assert calls[0]==('systemctl','stop','gdm.service')
    assert calls[-1]==('systemctl','start','gdm.service')
    assert calls.index(('systemctl','daemon-reload')) < calls.index(('modprobe','-r','nvidia_drm'))


def test_recovery_preserves_operator_changes_without_stopping_desktop(installed):
    meta,module,helper,base,calls=installed
    module.write_text('operator edit')
    with pytest.raises(RuntimeError,match='changed'):guard.recover(meta)
    assert calls==[] and helper.exists() and module.read_text()=='operator edit'


def test_gdm_start_is_attempted_even_if_daemon_reload_fails(installed,monkeypatch):
    meta,module,helper,base,calls=installed
    def command(*args,**kw):
        calls.append(args)
        if args==('systemctl','daemon-reload'):raise RuntimeError('reload failure')
    monkeypatch.setattr(guard,'command',command)
    with pytest.raises(RuntimeError,match='reload failure'):guard.recover(meta)
    assert calls[-1]==('systemctl','start','gdm.service')

import json
import pytest
from app.tools.contact_cli import main


def test_set_info_preserves_phone_and_unicode(tmp_path, monkeypatch):
    answers = iter([' Testovací kontakt ', ' 0910000000 ', ' test@example.com '])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    path = tmp_path / 'data' / 'contact.json'
    assert main(['set-info'], contact_path=path) == 0
    assert json.loads(path.read_text()) == dict(name='Testovací kontakt', phone='0910000000', email='test@example.com')
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob('.contact-*'))


@pytest.mark.parametrize('interrupted', [False, True])
def test_invalid_or_cancelled_input_preserves_existing_contact(tmp_path, monkeypatch, interrupted):
    path = tmp_path / 'contact.json'
    path.write_text('original')
    def answer(_):
        if interrupted:
            raise EOFError()
        return ' '
    monkeypatch.setattr('builtins.input', answer)
    assert main(['set-info'], contact_path=path) == 1
    assert path.read_text() == 'original'

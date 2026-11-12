from unittest.mock import Mock
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QSpinBox, QLabel
from app.ui.system_keyboard import SystemKeyboard


def test_editable_fields_and_readonly_are_distinguished():
    app = QApplication.instance() or QApplication([])
    field = QLineEdit(); readonly = QLineEdit(); readonly.setReadOnly(True)
    assert SystemKeyboard.editable(field)
    assert not SystemKeyboard.editable(readonly)
    assert SystemKeyboard.editable(QTextEdit())
    assert SystemKeyboard.editable(QSpinBox())
    assert not SystemKeyboard.editable(QLabel())
    assert not SystemKeyboard.editable(None)
    field.setEnabled(False)
    assert not SystemKeyboard.editable(field)


def test_field_touch_requests_native_keyboard_without_transmitting_text(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.delenv('IBUS_ADDRESS', raising=False)
    manager = SystemKeyboard(app)
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setText('Do-not-transmit-this-password')
    field.show(); app.processEvents()
    try:
        manager.request(field)
        assert manager._pending['op'] == 'show'
        assert set(manager._pending) == {'op', 'rect'}
        assert 'Do-not-transmit' not in str(manager._pending)
        manager.request = Mock()
        QTest.mouseClick(field, Qt.LeftButton); app.processEvents()
        manager.request.assert_any_call(field)
    finally:
        field.close(); manager.close(); manager.deleteLater(); app.processEvents()

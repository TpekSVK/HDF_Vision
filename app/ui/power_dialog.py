"""Shared-password power menu using the desktop on-screen keyboard."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QLabel, QLineEdit,
                              QPushButton, QVBoxLayout, QWidget)


class PowerDialog(QDialog):
    def __init__(self, security, parent=None):
        super().__init__(parent)
        self.security = security
        self.action = None
        self._authenticated = False
        self.setWindowTitle('Napájanie')
        self.setObjectName('powerDialog')
        self.setModal(True)
        self.resize(680, 300)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel('Napájanie a pracovná plocha')
        title.setProperty('role', 'appTitle')
        layout.addWidget(title)
        self.locked = QWidget()
        locked_layout = QVBoxLayout(self.locked)
        locked_layout.setContentsMargins(0, 0, 0, 0)
        locked_layout.addWidget(QLabel('Zadajte rovnaké heslo ako pri ochrane receptov.'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setAccessibleName('Heslo ochrany receptov a napájania')
        self.password.returnPressed.connect(self._unlock)
        locked_layout.addWidget(self.password)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        self.error.setProperty('status', 'nok')
        locked_layout.addWidget(self.error)
        self.unlock_button = QPushButton('Odomknúť')
        self.unlock_button.setProperty('role', 'primary')
        self.unlock_button.clicked.connect(self._unlock)
        locked_layout.addWidget(self.unlock_button)
        layout.addWidget(self.locked)
        self.actions = QWidget()
        actions_layout = QVBoxLayout(self.actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel('Akcia bezpečne ukončí všetky kamerové stanice.')
        hint.setWordWrap(True)
        actions_layout.addWidget(hint)
        self.action_buttons = {}
        for action, label in [('exit', 'Zavrieť aplikáciu · pracovná plocha'),
                              ('reboot', 'Reštartovať počítač'), ('shutdown', 'Vypnúť počítač')]:
            button = QPushButton(label)
            if action == 'shutdown':
                button.setProperty('role', 'destructive')
            button.clicked.connect(lambda checked=False, a=action: self._choose(a))
            actions_layout.addWidget(button)
            self.action_buttons[action] = button
        layout.addWidget(self.actions)
        cancel = QPushButton('Zrušiť')
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self._refresh_lock()

    def _refresh_lock(self):
        unlocked = self._authenticated or not self.security.requires_password()
        self.locked.setVisible(not unlocked)
        self.actions.setVisible(unlocked)
        if not unlocked:
            self.password.setFocus(Qt.OtherFocusReason)

    def _unlock(self):
        if not self.security.requires_password() or self.security.verify_password(self.password.text()):
            self._authenticated = True
            self.password.clear()
            self._refresh_lock()
        else:
            self.error.setText('Nesprávne heslo. Skúste to znova.')
            self.password.clear()
            self.password.setFocus(Qt.OtherFocusReason)

    def _choose(self, action):
        # Recheck if protection was enabled from a terminal while the menu was open.
        if self.security.requires_password() and not self._authenticated:
            self._refresh_lock()
            return
        self.action = action
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.locked.isHidden():
            # Keep password and confirmation above the native keyboard.
            screen = self.screen().availableGeometry()
            self.move(screen.center().x()-self.width()//2, screen.top()+40)
            self.password.setFocus(Qt.OtherFocusReason)

    def done(self, result):
        self.password.clear()
        self._authenticated = False
        super().done(result)

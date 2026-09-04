"""Reusable authorization prompt for recipe writes."""

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QWidget

from app.services.security_service import SecurityService


def authorize_recipe_write(parent: QWidget, security: SecurityService) -> bool:
    """Authorize before a write; ADMIN and unprotected modes never show a dialog."""
    if not security.requires_password():
        return True
    password, accepted = QInputDialog.getText(
        parent, "Ochrana receptu", "Zadajte heslo", QLineEdit.Password
    )
    if not accepted:
        return False
    if security.verify_password(password):
        return True
    QMessageBox.warning(
        parent,
        "Nesprávne heslo",
        "Nesprávne heslo.\nZmena nebola uložená.",
    )
    return False

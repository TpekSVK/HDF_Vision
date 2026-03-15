"""Dialóg nastavení relácie pre Golden Wizard."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
    QVBoxLayout,
)

from app.services import settings_service


class SessionSettingsDialog(QDialog):
    """Modal dialog for editing runtime session toggles."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nastavenia relácie")
        self.setModal(True)

        self._settings = settings_service.get_session_settings()

        layout = QVBoxLayout(self)

        self._logging_checkbox = QCheckBox("Zapnúť ukladanie záznamov pre nové behy", self)
        self._logging_checkbox.setChecked(bool(self._settings.logging_enabled))

        layout.addWidget(self._logging_checkbox)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        layout.addLayout(form)

        target_dir = self._settings.logging_path
        if not isinstance(target_dir, Path):
            target_dir = Path(str(target_dir))
        if isinstance(target_dir, Path) and target_dir.suffix:
            target_dir = target_dir.parent

        self._path_edit = QLineEdit(str(target_dir), self)
        self._path_edit.setPlaceholderText("/data/logs")
        browse_btn = QPushButton("Prehľadávať…", self)
        browse_btn.clicked.connect(self._on_browse_clicked)

        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)

        path_widget = QWidget(self)
        path_widget.setLayout(path_row)
        form.addRow("Cieľový priečinok", path_widget)

        self._artifacts_checkbox = QCheckBox("Exportovať zarovnaný obrázok (PNG)", self)
        self._artifacts_checkbox.setChecked(bool(self._settings.export_artifacts))
        form.addRow("Výstupy", self._artifacts_checkbox)

        self._overlay_checkbox = QCheckBox("Pridať prekrytie (PNG)", self)
        self._overlay_checkbox.setChecked(bool(self._settings.export_overlay))
        form.addRow("Prekrytie", self._overlay_checkbox)

        self._debug_overlay_checkbox = QCheckBox(
            "Zobraziť debug overlay výkonu",
            self,
        )
        self._debug_overlay_checkbox.setToolTip("Show performance debug overlay")
        self._debug_overlay_checkbox.setChecked(
            bool(self._settings.show_performance_debug_overlay)
        )
        form.addRow("Debug overlay", self._debug_overlay_checkbox)

        self._artifacts_checkbox.toggled.connect(self._on_artifacts_toggled)
        self._on_artifacts_toggled(self._artifacts_checkbox.isChecked())

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_browse_clicked(self) -> None:
        current = self._path_edit.text().strip() or str(self._settings.logging_path)
        directory = QFileDialog.getExistingDirectory(
            self, "Vyberte cieľový priečinok", current
        )
        if directory:
            self._path_edit.setText(directory)

    def _on_artifacts_toggled(self, enabled: bool) -> None:
        self._overlay_checkbox.setEnabled(bool(enabled))

    def _on_accept(self) -> None:
        path_text = self._path_edit.text().strip()
        if not path_text:
            QMessageBox.warning(self, "Chýba cesta", "Prosím, zadajte cieľový priečinok.")
            return

        target = Path(path_text).expanduser()
        if target.exists() and not target.is_dir():
            QMessageBox.critical(self, "Neplatná cesta", "Cieľ musí byť priečinok.")
            return

        if not target.exists():
            answer = QMessageBox.question(
                self,
                "Vytvoriť priečinok?",
                f"Priečinok '{target}' neexistuje.\nChcete ho vytvoriť?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Vytvorenie zlyhalo",
                    f"Priečinok sa nepodarilo vytvoriť:\n{exc}",
                )
                return

        try:
            settings_service.update_session_settings(
                logging_enabled=self._logging_checkbox.isChecked(),
                logging_path=target,
                export_artifacts=self._artifacts_checkbox.isChecked(),
                export_overlay=(
                    self._overlay_checkbox.isChecked()
                    and self._artifacts_checkbox.isChecked()
                ),
                show_performance_debug_overlay=self._debug_overlay_checkbox.isChecked(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.critical(self, "Aktualizácia zlyhala", str(exc))
            return

        self.accept()


__all__ = ["SessionSettingsDialog"]

"""Dialog windows for configuring recipe views in the Golden Wizard."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
    QVBoxLayout,
)

from app.models.schema import ViewCameraProfile

_DEFAULT_CAMERA_RESOLUTIONS: Sequence[tuple[str, dict[str, Any]]] = (
    (
        "1920x1080@60 Y8",
        {"width": 1920, "height": 1080, "fps": 60, "pixel_format": "Y8"},
    ),
    (
        "1280x720@60 Y8",
        {"width": 1280, "height": 720, "fps": 60, "pixel_format": "Y8"},
    ),
    (
        "2592x1944@30 Y8 (len setup/pomalé)",
        {"width": 2592, "height": 1944, "fps": 30, "pixel_format": "Y8"},
    ),
)


class ViewConfigDialog(QDialog):
    """Dialog that gathers configuration for a recipe view."""

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
        mode: str = "add",
        view_id: str,
        name: str,
        available_resolutions: Sequence[tuple[str, dict[str, Any]]],
        current_camera: Optional[dict[str, Any]] = None,
        camera_profile: ViewCameraProfile | dict | str | None = None,
        camera_model: Optional[str] = None,
        supported_v4l2_controls: Optional[set[str]] = None,
        settle_ms: Optional[int] = None,
        trigger_mode: str = "timed",
        trigger_interval_ms: Optional[int] = None,
        available_frame_sources: Sequence[tuple[str, str]] | None = None,
        frame_source_view_id: Optional[str] = None,
        available_branch_targets: Sequence[tuple[str, str]] | None = None,
        branch_enabled: bool = False,
        branch_targets: Optional[dict[str, str]] = None,
        branch_default_view_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._view_id = view_id
        self._available_resolutions = list(available_resolutions)
        self._available_frame_sources = list(available_frame_sources or [])
        self._available_branch_targets = list(available_branch_targets or [])
        self._result: Optional[dict[str, Any]] = None
        self._camera_model = str(camera_model or "").strip()
        controls = {str(c).strip() for c in (supported_v4l2_controls or set()) if str(c).strip()}
        if "see3cam" in self._camera_model.lower() and "cu55" in self._camera_model.lower():
            controls = {"brightness", "exposure_time_absolute"}
        if not controls:
            controls = {"brightness", "exposure_time_absolute", "exposure_absolute", "gain", "gamma", "sharpness"}
        self._supported_v4l2_controls = controls
        self._supports_gain = "gain" in controls
        self._supports_gamma = "gamma" in controls
        self._supports_brightness = "brightness" in controls
        self._supports_sharpness = "sharpness" in controls

        title = "Add View" if mode == "add" else "Edit View"
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        basic_group = QGroupBox("Základné informácie", self)
        basic_form = QFormLayout(basic_group)
        basic_form.setSpacing(6)
        self._name_edit = QLineEdit(name, basic_group)
        self._name_edit.setPlaceholderText("Názov view (povinné)")
        self._id_label = QLabel(view_id, basic_group)
        self._id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        basic_form.addRow("Name:", self._name_edit)
        name_hint = self._create_description_label(
            "Názov view-u, ktorý sa zobrazí v prehľade Golden Wizardu aj v multi-view."
            " Uľahčuje prepínanie medzi náhľadmi, preto by mal byť v rámci receptu"
            " jedinečný.",
            basic_group,
        )
        self._name_edit.setToolTip(name_hint.text())
        basic_form.addRow(name_hint)

        basic_form.addRow("ID:", self._id_label)
        id_hint = self._create_description_label(
            "Nemenné systémové ID view-u. Používa sa pri ukladaní golden dát a"
            " synchronizácii v multi-view, preto sa nemení ani pri premenovaní.",
            basic_group,
        )
        self._id_label.setToolTip(id_hint.text())
        basic_form.addRow(id_hint)
        layout.addWidget(basic_group)

        camera_group = QGroupBox("Camera settings pre tento view", self)
        camera_form = QFormLayout(camera_group)
        camera_form.setSpacing(6)

        self._resolution_combo = QComboBox(camera_group)
        self._populate_resolution_combo(current_camera, camera_profile)
        camera_form.addRow("Resolution:", self._resolution_combo)
        resolution_hint = self._create_description_label(
            "Rozlíšenie, snímková frekvencia a pixel formát pre tento view."
            " Voľba „Inherit“ ponechá parametre z globálnej kamery alebo z"
            " multi-view profilu; konkrétna voľba ovplyvní iba aktuálny view.",
            camera_group,
        )
        self._resolution_combo.setToolTip(resolution_hint.text())
        camera_form.addRow(resolution_hint)

        self._exposure_edit = QLineEdit(camera_group)
        self._exposure_edit.setPlaceholderText("Leave blank to inherit")
        self._gain_edit = QLineEdit(camera_group)
        self._gain_edit.setPlaceholderText("Leave blank to inherit")
        self._pixel_format_combo = QComboBox(camera_group)
        self._pixel_format_combo.addItem("Inherit", None)
        self._pixel_format_combo.addItem("Y8", "Y8")
        self._pixel_format_combo.addItem("Y12", "Y12")
        self._device_edit = QLineEdit(camera_group)
        self._device_edit.setPlaceholderText("/dev/video0 (optional)")
        self._gamma_edit = QLineEdit(camera_group)
        self._gamma_edit.setPlaceholderText("Leave blank to inherit")
        self._brightness_edit = QLineEdit(camera_group)
        self._brightness_edit.setPlaceholderText("Leave blank to inherit")
        self._sharpness_edit = QLineEdit(camera_group)
        self._sharpness_edit.setPlaceholderText("Leave blank to inherit")
        self._stream_mode_combo = QComboBox(camera_group)
        self._stream_mode_combo.addItem("Inherit", None)
        self._stream_mode_combo.addItem("Master (0)", 0)
        self._stream_mode_combo.addItem("Trigger (1)", 1)
        self._flash_mode_combo = QComboBox(camera_group)
        self._flash_mode_combo.addItem("Inherit", None)
        self._flash_mode_combo.addItem("OFF (0)", 0)
        self._flash_mode_combo.addItem("STROBE (1)", 1)
        self._flash_mode_combo.addItem("TORCH (2)", 2)
        camera_form.addRow("Camera device:", self._device_edit)
        camera_form.addRow("Exposure [µs]:", self._exposure_edit)
        exposure_hint = self._create_description_label(
            "Prepíše expozičný čas kamery len pre tento view. Prázdna hodnota"
            " znamená zdedenie aktuálneho nastavenia; v multi-view sa použije"
            " vždy pri aktivácii tohto view.",
            camera_group,
        )
        self._exposure_edit.setToolTip(exposure_hint.text())
        camera_form.addRow(exposure_hint)

        if self._supports_gain:
            camera_form.addRow("Gain [dB]:", self._gain_edit)
            gain_hint = self._create_description_label(
                "Prepíše zosilnenie (gain) pre aktuálny view. Nechané prázdne zdedí"
                " hodnotu z kamery; v multi-view má každé view vlastnú uloženú"
                " kombináciu.",
                camera_group,
            )
            self._gain_edit.setToolTip(gain_hint.text())
            camera_form.addRow(gain_hint)

        camera_form.addRow("Pixel Format:", self._pixel_format_combo)
        if self._supports_gamma:
            camera_form.addRow("Gamma:", self._gamma_edit)
        if self._supports_brightness:
            camera_form.addRow("Brightness:", self._brightness_edit)
        if self._supports_sharpness:
            camera_form.addRow("Sharpness:", self._sharpness_edit)
        camera_form.addRow("Stream Mode:", self._stream_mode_combo)
        camera_form.addRow("Flash Mode:", self._flash_mode_combo)
        pixel_hint = self._create_description_label(
            "Vyberá pixelový formát streamu. „Inherit“ znamená, že sa použije"
            " formát zdieľaný s ostatnými view v multi-view; konkrétna voľba"
            " ovplyvní iba aktuálny view.",
            camera_group,
        )
        self._pixel_format_combo.setToolTip(pixel_hint.text())
        camera_form.addRow(pixel_hint)
        layout.addWidget(camera_group)

        timing_group = QGroupBox("Časovanie snímania", self)
        timing_form = QFormLayout(timing_group)
        timing_form.setSpacing(6)

        self._settle_edit = QLineEdit(timing_group)
        self._settle_edit.setPlaceholderText("Leave blank to inherit")
        timing_form.addRow("Settle Time (ms):", self._settle_edit)
        settle_hint = self._create_description_label(
            "Čas na ustálenie kamery po prepnutí do view pred zachytením"
            " snímky. Prázdne = zdedená hodnota; v multi-view sa dodrží pri"
            " každom cykle, keď sa view aktivuje.",
            timing_group,
        )
        self._settle_edit.setToolTip(settle_hint.text())
        timing_form.addRow(settle_hint)

        self._trigger_mode_combo = QComboBox(timing_group)
        self._trigger_mode_combo.addItem("Timed", "timed")
        self._trigger_mode_combo.addItem("External Trigger", "external")
        self._trigger_mode_combo.addItem("Manual Trigger (GPIO)", "manual")
        self._trigger_mode_combo.currentIndexChanged.connect(
            self._on_trigger_mode_changed
        )
        timing_form.addRow("Trigger Mode:", self._trigger_mode_combo)
        trigger_hint = self._create_description_label(
            "Určuje, ako sa spúšťa snímanie: Timed používa interný časovač"
            " (v multi-view beží v cykle), External čaká na hardvérový impulz"
            " a Manual reaguje na TRIGGER tlačidlo alebo GPIO vstup v RUN režime.",
            timing_group,
        )
        self._trigger_mode_combo.setToolTip(trigger_hint.text())
        timing_form.addRow(trigger_hint)

        self._interval_edit = QLineEdit(timing_group)
        self._interval_edit.setPlaceholderText("Required for Timed mode")
        timing_form.addRow("Interval (ms):", self._interval_edit)
        interval_hint = self._create_description_label(
            "Interval po dokončení snímky v režime Timed. V multi-view určuje, ako"
            " o koľko neskôr sa spustí ďalší view; ostatné view pokračujú podľa svojich"
            " nastavení.",
            timing_group,
        )
        self._interval_edit.setToolTip(interval_hint.text())
        timing_form.addRow(interval_hint)

        self._frame_source_combo = QComboBox(timing_group)
        self._frame_source_combo.addItem("Samostatný záber (default)", None)
        for view_id, label in self._available_frame_sources:
            display = label if label else view_id
            self._frame_source_combo.addItem(display, view_id)
        timing_form.addRow("Zdroj snímky:", self._frame_source_combo)
        frame_hint = self._create_description_label(
            "Vyber ID iného view-u, z ktorého sa použije posledný záber namiesto"
            " spúšťania vlastného triggeru. Prázdne = samostatné snímanie.",
            timing_group,
        )
        self._frame_source_combo.setToolTip(frame_hint.text())
        timing_form.addRow(frame_hint)
        layout.addWidget(timing_group)

        branching_group = QGroupBox("Vetvenie snímky (voliteľné)", self)
        branching_form = QFormLayout(branching_group)
        branching_form.setSpacing(6)

        self._branch_enabled_checkbox = QCheckBox(
            "Povoliť presmerovanie na iný view podľa výsledku", branching_group
        )
        self._branch_enabled_checkbox.setChecked(bool(branch_enabled))
        branching_form.addRow(self._branch_enabled_checkbox)

        self._branch_ok_combo = self._create_branch_combo(branching_group)
        self._branch_warn_combo = self._create_branch_combo(branching_group)
        self._branch_nok_combo = self._create_branch_combo(branching_group)
        self._branch_default_combo = self._create_branch_combo(branching_group)

        branching_form.addRow("Cieľ pre OK:", self._branch_ok_combo)
        branching_form.addRow("Cieľ pre WARN:", self._branch_warn_combo)
        branching_form.addRow("Cieľ pre NOK:", self._branch_nok_combo)
        branching_form.addRow("Základný cieľ (fallback):", self._branch_default_combo)

        branch_hint = self._create_description_label(
            "Ak je vetvenie zapnuté, podľa statusu prvého nástroja sa vyberie"
            " cieľový view. Ostatné view sa v aktuálnom cykle preskočia.",
            branching_group,
        )
        branching_form.addRow(branch_hint)
        self._branch_enabled_checkbox.stateChanged.connect(
            self._on_branch_enabled_changed
        )
        layout.addWidget(branching_group)

        button_box = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        action_text = "Add View" if mode == "add" else "Save"
        self._accept_button = button_box.addButton(
            action_text, QDialogButtonBox.AcceptRole
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._apply_initial_profile(camera_profile)
        self._apply_initial_timing(settle_ms, trigger_mode, trigger_interval_ms)
        self._apply_initial_frame_source(frame_source_view_id)
        self._apply_initial_branch_targets(
            branch_enabled, branch_targets or {}, branch_default_view_id
        )

    @staticmethod
    def _create_description_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setWordWrap(True)
        label.setStyleSheet("color: #5b5b5b; font-size: 11px;")
        label.setContentsMargins(0, 0, 0, 6)
        return label

    def view_id(self) -> str:
        return self._view_id

    def values(self) -> dict[str, Any]:
        return dict(self._result or {})

    def accept(self) -> None:  # noqa: N802 - Qt API
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.critical(self, "Invalid input", "Name is required.")
            return

        try:
            settle_ms = self._parse_optional_int(self._settle_edit.text())
        except ValueError:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Settle Time must be an integer value.",
            )
            return

        try:
            exposure_us = self._parse_optional_int(self._exposure_edit.text())
        except ValueError:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Exposure must be an integer value.",
            )
            return

        try:
            gain_db = self._parse_optional_float(self._gain_edit.text()) if self._supports_gain else None
            gamma = self._parse_optional_float(self._gamma_edit.text()) if self._supports_gamma else None
            brightness = self._parse_optional_float(self._brightness_edit.text()) if self._supports_brightness else None
            sharpness = self._parse_optional_float(self._sharpness_edit.text()) if self._supports_sharpness else None
        except ValueError:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Gain must be a numeric value.",
            )
            return

        trigger_mode = str(self._trigger_mode_combo.currentData() or "timed")
        trigger_mode = (
            trigger_mode if trigger_mode in {"timed", "external", "manual"} else "timed"
        )

        try:
            interval_ms = self._parse_optional_int(self._interval_edit.text())
        except ValueError:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Interval must be an integer value.",
            )
            return

        if trigger_mode == "timed" and interval_ms is None:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Interval is required in Timed trigger mode.",
            )
            return
        if trigger_mode != "timed":
            interval_ms = None

        profile = self._build_camera_profile(exposure_us, gain_db, gamma, brightness, sharpness)

        self._result = {
            "name": name,
            "camera_profile": profile,
            "settle_ms": settle_ms,
            "trigger_mode": trigger_mode,
            "trigger_interval_ms": interval_ms,
            "frame_source_view_id": self._frame_source_combo.currentData(),
            "branch_enabled": self._branch_enabled_checkbox.isChecked(),
            "branch_targets": self._collect_branch_targets(),
            "branch_default_view_id": self._branch_default_combo.currentData(),
        }
        super().accept()

    def _create_branch_combo(self, parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItem("Bez presmerovania", None)
        for view_id, label in self._available_branch_targets:
            display = label if label else view_id
            combo.addItem(display, view_id)
        combo.setEnabled(False)
        return combo

    def _populate_resolution_combo(
        self,
        current_camera: Optional[dict[str, Any]],
        camera_profile: ViewCameraProfile | dict | str | None,
    ) -> None:
        self._resolution_combo.clear()
        self._resolution_combo.addItem("Inherit", None)

        if current_camera:
            label = self._format_resolution_label(current_camera, prefix="Current: ")
            self._resolution_combo.addItem(label, dict(current_camera))

        for label, data in self._available_resolutions:
            self._resolution_combo.addItem(label, dict(data))

        profile_obj = camera_profile
        if isinstance(profile_obj, dict):
            profile_obj = ViewCameraProfile.from_obj(profile_obj)

        if isinstance(profile_obj, ViewCameraProfile):
            if profile_obj.width and profile_obj.height and profile_obj.fps:
                target = {
                    "width": profile_obj.width,
                    "height": profile_obj.height,
                    "fps": profile_obj.fps,
                    "pixel_format": profile_obj.pixel_format,
                }
                index = self._match_resolution_index(target)
                if index is not None:
                    self._resolution_combo.setCurrentIndex(index)
                else:
                    label = self._format_resolution_label(target, prefix="Custom: ")
                    self._resolution_combo.addItem(label, target)
                    self._resolution_combo.setCurrentIndex(self._resolution_combo.count() - 1)

    def _match_resolution_index(self, target: dict[str, Any]) -> Optional[int]:
        width = int(target.get("width", 0))
        height = int(target.get("height", 0))
        fps = int(target.get("fps", 0))
        pix = str(target.get("pixel_format", "") or "").upper()
        for idx in range(self._resolution_combo.count()):
            data = self._resolution_combo.itemData(idx)
            if not isinstance(data, dict):
                continue
            if (
                int(data.get("width", 0)) == width
                and int(data.get("height", 0)) == height
                and int(data.get("fps", 0)) == fps
                and str(data.get("pixel_format", "") or "").upper() == pix
            ):
                return idx
        return None

    @staticmethod
    def _format_resolution_label(data: dict[str, Any], *, prefix: str = "") -> str:
        width = data.get("width") or "?"
        height = data.get("height") or "?"
        fps = data.get("fps") or "?"
        pix = (data.get("pixel_format") or "").upper() or "?"
        return f"{prefix}{width}x{height}@{fps} {pix}"

    def _apply_initial_profile(
        self, camera_profile: ViewCameraProfile | dict | str | None
    ) -> None:
        profile_obj = camera_profile
        if isinstance(profile_obj, dict):
            profile_obj = ViewCameraProfile.from_obj(profile_obj)

        if isinstance(profile_obj, ViewCameraProfile):
            if profile_obj.exposure_us is not None:
                self._exposure_edit.setText(str(int(profile_obj.exposure_us)))
            if self._supports_gain and profile_obj.gain_db is not None:
                self._gain_edit.setText(str(profile_obj.gain_db))
            if profile_obj.device_id:
                self._device_edit.setText(str(profile_obj.device_id))
            if profile_obj.pixel_format:
                index = self._pixel_format_combo.findData(profile_obj.pixel_format)
                if index >= 0:
                    self._pixel_format_combo.setCurrentIndex(index)
            if self._supports_gamma and profile_obj.gamma is not None:
                self._gamma_edit.setText(str(profile_obj.gamma))
            if self._supports_brightness and profile_obj.brightness is not None:
                self._brightness_edit.setText(str(profile_obj.brightness))
            if self._supports_sharpness and profile_obj.sharpness is not None:
                self._sharpness_edit.setText(str(profile_obj.sharpness))
            if profile_obj.stream_mode is not None:
                index = self._stream_mode_combo.findData(profile_obj.stream_mode)
                if index >= 0:
                    self._stream_mode_combo.setCurrentIndex(index)
            if profile_obj.flash_mode is not None:
                index = self._flash_mode_combo.findData(profile_obj.flash_mode)
                if index >= 0:
                    self._flash_mode_combo.setCurrentIndex(index)

    def _apply_initial_timing(
        self,
        settle_ms: Optional[int],
        trigger_mode: str,
        trigger_interval_ms: Optional[int],
    ) -> None:
        if settle_ms is not None:
            self._settle_edit.setText(str(int(settle_ms)))

        normalized_mode = str(trigger_mode or "timed").strip().lower()
        if normalized_mode not in {"timed", "external", "manual"}:
            normalized_mode = "timed"
        index = self._trigger_mode_combo.findData(normalized_mode)
        if index >= 0:
            self._trigger_mode_combo.setCurrentIndex(index)

        if trigger_interval_ms is not None:
            self._interval_edit.setText(str(int(trigger_interval_ms)))
        self._on_trigger_mode_changed()

    def _apply_initial_frame_source(
        self, frame_source_view_id: Optional[str]
    ) -> None:
        if not frame_source_view_id:
            return
        index = self._frame_source_combo.findData(frame_source_view_id)
        if index >= 0:
            self._frame_source_combo.setCurrentIndex(index)

    def _apply_initial_branch_targets(
        self,
        branch_enabled: bool,
        branch_targets: dict[str, str],
        branch_default_view_id: Optional[str],
    ) -> None:
        self._branch_enabled_checkbox.setChecked(bool(branch_enabled))
        for status, combo in (
            ("ok", self._branch_ok_combo),
            ("warn", self._branch_warn_combo),
            ("nok", self._branch_nok_combo),
        ):
            target = branch_targets.get(status)
            if not target:
                continue
            index = combo.findData(target)
            if index >= 0:
                combo.setCurrentIndex(index)
        if branch_default_view_id:
            index = self._branch_default_combo.findData(branch_default_view_id)
            if index >= 0:
                self._branch_default_combo.setCurrentIndex(index)
        self._on_branch_enabled_changed()

    def _on_branch_enabled_changed(self) -> None:
        enabled = self._branch_enabled_checkbox.isChecked()
        for combo in (
            self._branch_ok_combo,
            self._branch_warn_combo,
            self._branch_nok_combo,
            self._branch_default_combo,
        ):
            combo.setEnabled(enabled)

    def _collect_branch_targets(self) -> dict[str, str]:
        if not self._branch_enabled_checkbox.isChecked():
            return {}
        collected: dict[str, str] = {}
        for status, combo in (
            ("ok", self._branch_ok_combo),
            ("warn", self._branch_warn_combo),
            ("nok", self._branch_nok_combo),
        ):
            target = combo.currentData()
            if target:
                collected[status] = target
        return collected

    def _on_trigger_mode_changed(self) -> None:
        mode = str(self._trigger_mode_combo.currentData() or "timed")
        self._interval_edit.setEnabled(mode == "timed")

    def _build_camera_profile(
        self,
        exposure_us: Optional[int],
        gain_db: Optional[float],
        gamma: Optional[float],
        brightness: Optional[float],
        sharpness: Optional[float],
    ) -> Optional[ViewCameraProfile]:
        data: dict[str, Any] = {}
        resolution_data = self._resolution_combo.currentData()
        if isinstance(resolution_data, dict):
            for key in ("width", "height", "fps", "pixel_format"):
                value = resolution_data.get(key)
                if value is not None and value != "":
                    data[key] = value

        pixel_format = self._pixel_format_combo.currentData()
        if pixel_format:
            data["pixel_format"] = pixel_format

        device_id = self._device_edit.text().strip()
        if device_id:
            data["device_id"] = device_id
        if exposure_us is not None:
            data["exposure_us"] = exposure_us
        if self._supports_gain and gain_db is not None:
            data["gain_db"] = gain_db
        if self._supports_gamma and gamma is not None:
            data["gamma"] = gamma
        if self._supports_brightness and brightness is not None:
            data["brightness"] = brightness
        if self._supports_sharpness and sharpness is not None:
            data["sharpness"] = sharpness
        stream_mode = self._stream_mode_combo.currentData()
        if stream_mode is not None:
            data["stream_mode"] = int(stream_mode)
        flash_mode = self._flash_mode_combo.currentData()
        if flash_mode is not None:
            data["flash_mode"] = int(flash_mode)

        profile = ViewCameraProfile.from_obj(data)
        if isinstance(profile, ViewCameraProfile) and not profile.is_empty():
            return profile
        return None

    @staticmethod
    def _parse_optional_int(text: str) -> Optional[int]:
        stripped = text.strip()
        if not stripped:
            return None
        return int(stripped)

    @staticmethod
    def _parse_optional_float(text: str) -> Optional[float]:
        stripped = text.strip().replace(",", ".")
        if not stripped:
            return None
        return float(stripped)


__all__ = ["ViewConfigDialog", "_DEFAULT_CAMERA_RESOLUTIONS"]

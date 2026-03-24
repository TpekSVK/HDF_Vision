"""Dialog windows for configuring recipe views in the Golden Wizard."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QWidget,
    QVBoxLayout,
)

from app.models.schema import ViewCameraProfile
from app.utils.trigger_timing import get_default_trigger_gap_ms, get_trigger_min_period_ms

_DEFAULT_CAMERA_RESOLUTIONS: Sequence[tuple[str, dict[str, Any]]] = (
    (
        "1920x1080@60 Y8 [BUG: trigger mode momentálne nefunkčné]",
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


_TRIGGER_RESOLUTION_WARNINGS: dict[tuple[int, int, int, str], str] = {
    (1920, 1080, 60, "Y8"): "BUG: 1920x1080@60 Y8 je momentálne nefunkčné v trigger režime.",
}

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
        external_trigger_mode: Optional[str] = None,
        external_request_input: Optional[int] = None,
        trigger_interval_ms: Optional[int] = None,
        trigger_gap_ms: Optional[int | float] = None,
        available_frame_sources: Sequence[tuple[str, str]] | None = None,
        frame_source_view_id: Optional[str] = None,
        available_branch_targets: Sequence[tuple[str, str]] | None = None,
        branch_enabled: bool = False,
        branch_targets: Optional[dict[str, str]] = None,
        branch_default_view_id: Optional[str] = None,
        image_rotation: int = 0,
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
        self._trigger_pulse_ms = 10.0
        self._image_rotation = 0

        title = "Pridať pohľad" if mode == "add" else "Upraviť pohľad"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(640, 480)
        screen = QApplication.primaryScreen()
        max_height = int(screen.availableGeometry().height() * 0.85) if screen else 760
        self.resize(760, min(760, max_height))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        content_scroll = QScrollArea(self)
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget(content_scroll)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(8)
        content_scroll.setWidget(content_widget)
        layout.addWidget(content_scroll, 1)

        basic_group = QGroupBox("Základné informácie", content_widget)
        basic_form = QFormLayout(basic_group)
        self._setup_compact_form(basic_form)
        self._name_edit = QLineEdit(name, basic_group)
        self._name_edit.setPlaceholderText("Názov view (povinné)")
        self._id_label = QLabel(view_id, basic_group)
        self._id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        name_hint = (
            "Názov view-u, ktorý sa zobrazí v prehľade Golden Wizardu aj v multi-view."
            " Uľahčuje prepínanie medzi náhľadmi, preto by mal byť v rámci receptu"
            " jedinečný."
        )
        self._add_form_row(basic_form, "Name:", self._name_edit, tooltip=name_hint)

        id_hint = (
            "Nemenné systémové ID view-u. Používa sa pri ukladaní golden dát a"
            " synchronizácii v multi-view, preto sa nemení ani pri premenovaní."
        )
        self._add_form_row(basic_form, "ID:", self._id_label, tooltip=id_hint)
        content_layout.addWidget(basic_group)

        camera_group = QGroupBox("Nastavenia kamery pre tento view", content_widget)
        camera_form = QFormLayout(camera_group)
        self._setup_compact_form(camera_form)

        self._resolution_combo = QComboBox(camera_group)
        self._populate_resolution_combo(current_camera, camera_profile)
        resolution_hint = (
            "Rozlíšenie, snímková frekvencia a pixel formát pre tento view."
            " Voľba „Inherit“ ponechá parametre z globálnej kamery alebo z"
            " multi-view profilu; konkrétna voľba ovplyvní iba aktuálny view."
        )
        self._add_form_row(camera_form, "Resolution:", self._resolution_combo, tooltip=resolution_hint)

        self._exposure_edit = QLineEdit(camera_group)
        self._exposure_edit.setPlaceholderText("Leave blank to inherit")
        self._trigger_exposure_edit = QLineEdit(camera_group)
        self._trigger_exposure_edit.setPlaceholderText("Auto default for trigger mode")
        self._trigger_exposure_edit.textChanged.connect(lambda _text: self._update_trigger_gap_warning())
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
        self._flash_mode_combo = QComboBox(camera_group)
        self._flash_mode_combo.addItem("Inherit", None)
        self._flash_mode_combo.addItem("Vypnuté (0)", 0)
        self._flash_mode_combo.addItem("Stroboskop (1)", 1)
        self._flash_mode_combo.addItem("Svetlo natrvalo (2)", 2)
        self._add_form_row(camera_form, "Camera device:", self._device_edit)
        exposure_hint = (
            "Platí len pre master mode. V trigger mode sa táto hodnota"
            " nepoužíva na riadenie jasu."
        )
        self._add_form_row(camera_form, "Expozícia - master mode [us]:", self._exposure_edit, tooltip=exposure_hint)
        trigger_exposure_hint = (
            "Platí len pre trigger mode. V trigger mode riadi čas medzi"
            " trigger pulzmi (gap), a tým výsledný jas."
        )
        self._add_form_row(camera_form, "Expozícia - trigger mode [ms]:", self._trigger_exposure_edit, tooltip=trigger_exposure_hint)

        if self._supports_gain:
            gain_hint = (
                "Prepíše zosilnenie (gain) pre aktuálny view. Nechané prázdne zdedí"
                " hodnotu z kamery; v multi-view má každé view vlastnú uloženú"
                " kombináciu."
            )
            self._add_form_row(camera_form, "Zisk [dB]:", self._gain_edit, tooltip=gain_hint)

        pixel_hint = (
            "Vyberá pixelový formát streamu. „Inherit“ znamená, že sa použije"
            " formát zdieľaný s ostatnými view v multi-view; konkrétna voľba"
            " ovplyvní iba aktuálny view."
        )
        self._add_form_row(camera_form, "Formát pixelov:", self._pixel_format_combo, tooltip=pixel_hint)
        if self._supports_gamma:
            self._add_form_row(camera_form, "Gamma:", self._gamma_edit)
        if self._supports_brightness:
            self._add_form_row(camera_form, "Brightness:", self._brightness_edit)
        if self._supports_sharpness:
            self._add_form_row(camera_form, "Sharpness:", self._sharpness_edit)
        self._add_form_row(camera_form, "Režim blesku:", self._flash_mode_combo)
        self._rotation_combo = QComboBox(camera_group)
        self._rotation_combo.addItem("0°", 0)
        self._rotation_combo.addItem("90°", 90)
        self._rotation_combo.addItem("180°", 180)
        self._rotation_combo.addItem("270°", 270)
        rotation_hint = (
            "Softvérová rotácia obrazu pre tento view pred zobrazením aj spracovaním."
        )
        self._add_form_row(camera_form, "Rotácia snímky:", self._rotation_combo, tooltip=rotation_hint)
        content_layout.addWidget(camera_group)

        timing_group = QGroupBox("Časovanie snímania", content_widget)
        timing_form = QFormLayout(timing_group)
        self._setup_compact_form(timing_form)

        self._settle_edit = QLineEdit(timing_group)
        self._settle_edit.setPlaceholderText("Leave blank to inherit")
        settle_hint = (
            "Čas na ustálenie kamery po prepnutí do view pred zachytením"
            " snímky. Prázdne = zdedená hodnota; v multi-view sa dodrží pri"
            " každom cykle, keď sa view aktivuje."
        )
        self._add_form_row(timing_form, "Settle Time:", self._settle_edit, tooltip=settle_hint)

        self._trigger_mode_combo = QComboBox(timing_group)
        self._trigger_mode_combo.addItem("Timed", "timed")
        self._trigger_mode_combo.addItem("External Trigger", "external")
        self._trigger_mode_combo.addItem("Manual Trigger (GPIO)", "manual")
        self._trigger_mode_combo.currentIndexChanged.connect(
            self._on_trigger_mode_changed
        )
        trigger_hint = (
            "Určuje, ako sa spúšťa snímanie: Timed používa interný časovač"
            " (v multi-view beží v cykle), External čaká na hardvérový impulz"
            " a Manual reaguje na TRIGGER tlačidlo alebo GPIO vstup v RUN režime."
        )
        self._add_form_row(timing_form, "Trigger Mode:", self._trigger_mode_combo, tooltip=trigger_hint)

        self._external_mode_label = QLabel("Externý režim:")
        self._external_mode_combo = QComboBox(timing_group)
        self._external_mode_combo.addItem("Sekvenčné", "sequential")
        self._external_mode_combo.addItem("Explicitné", "explicit")
        self._external_mode_combo.currentIndexChanged.connect(
            self._on_external_mode_changed
        )
        timing_form.addRow(self._external_mode_label, self._external_mode_combo)

        self._external_input_label = QLabel("Externý vstup:")
        self._external_input_combo = QComboBox(timing_group)
        self._external_input_combo.addItem("Nepoužité", None)
        for input_idx in range(1, 9):
            self._external_input_combo.addItem(f"Vstup {input_idx}", input_idx)
        timing_form.addRow(self._external_input_label, self._external_input_combo)

        self._interval_edit = QLineEdit(timing_group)
        self._interval_edit.setPlaceholderText("Required for Timed mode")
        interval_hint = (
            "Interval po dokončení snímky v režime Timed. V multi-view určuje, ako"
            " o koľko neskôr sa spustí ďalší view; ostatné view pokračujú podľa svojich"
            " nastavení."
        )
        self._add_form_row(timing_form, "Interval:", self._interval_edit, tooltip=interval_hint)

        self._frame_source_combo = QComboBox(timing_group)
        self._frame_source_combo.addItem("Samostatný záber (default)", None)
        for view_id, label in self._available_frame_sources:
            display = label if label else view_id
            self._frame_source_combo.addItem(display, view_id)
        frame_hint = (
            "Vyber ID iného view-u, z ktorého sa použije posledný záber namiesto"
            " spúšťania vlastného triggeru. Prázdne = samostatné snímanie."
        )
        self._add_form_row(timing_form, "Zdroj snímky:", self._frame_source_combo, tooltip=frame_hint)

        self._trigger_warning_label = QLabel("", timing_group)
        self._trigger_warning_label.setWordWrap(True)
        self._trigger_warning_label.setStyleSheet("color: #a05a00; font-size: 11px;")
        self._trigger_warning_label.setVisible(False)
        timing_form.addRow(self._trigger_warning_label)
        content_layout.addWidget(timing_group)

        branching_group = QGroupBox("Vetvenie snímky", content_widget)
        branching_form = QFormLayout(branching_group)
        self._setup_compact_form(branching_form)

        self._branch_enabled_checkbox = QCheckBox(
            "Povoliť presmerovanie na iný view podľa výsledku", branching_group
        )
        self._branch_enabled_checkbox.setChecked(bool(branch_enabled))
        self._branch_enabled_checkbox.setToolTip(
            "Ak je vetvenie zapnuté, podľa statusu prvého nástroja sa vyberie cieľový view."
            " Ostatné view sa v aktuálnom cykle preskočia."
        )
        branching_form.addRow(self._branch_enabled_checkbox)

        self._branch_ok_combo = self._create_branch_combo(branching_group)
        self._branch_warn_combo = self._create_branch_combo(branching_group)
        self._branch_nok_combo = self._create_branch_combo(branching_group)
        self._branch_default_combo = self._create_branch_combo(branching_group)

        self._add_form_row(branching_form, "Cieľ pre OK:", self._branch_ok_combo)
        self._add_form_row(branching_form, "Cieľ pre WARN:", self._branch_warn_combo)
        self._add_form_row(branching_form, "Cieľ pre NOK:", self._branch_nok_combo)
        self._add_form_row(branching_form, "Základný cieľ:", self._branch_default_combo)
        self._branch_enabled_checkbox.stateChanged.connect(
            self._on_branch_enabled_changed
        )
        content_layout.addWidget(branching_group)
        content_layout.addStretch(1)

        button_box = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        action_text = "Pridať pohľad" if mode == "add" else "Save"
        self._accept_button = button_box.addButton(
            action_text, QDialogButtonBox.AcceptRole
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._apply_initial_profile(camera_profile)
        self._apply_initial_rotation(image_rotation)
        self._apply_initial_timing(
            settle_ms,
            trigger_mode,
            external_trigger_mode,
            external_request_input,
            trigger_interval_ms,
            trigger_gap_ms,
        )
        self._apply_initial_frame_source(frame_source_view_id)
        self._apply_initial_branch_targets(
            branch_enabled, branch_targets or {}, branch_default_view_id
        )

    @staticmethod
    def _setup_compact_form(form: QFormLayout) -> None:
        form.setSpacing(4)
        form.setContentsMargins(8, 8, 8, 8)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    @staticmethod
    def _add_form_row(
        form: QFormLayout,
        label_text: str,
        field: QWidget,
        *,
        tooltip: str | None = None,
    ) -> None:
        label = QLabel(label_text)
        if tooltip:
            label.setToolTip(tooltip)
            field.setToolTip(tooltip)
        form.addRow(label, field)

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
            trigger_gap_ms = self._parse_optional_float(self._trigger_exposure_edit.text())
        except ValueError:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Expozícia v trigger mode musí byť číselná hodnota.",
            )
            return
        if trigger_gap_ms is not None and trigger_gap_ms <= 0:
            QMessageBox.critical(
                self,
                "Invalid input",
                "Expozícia v trigger mode musí byť väčšia ako 0 ms.",
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

        external_mode: Optional[str] = None
        external_input: Optional[int] = None
        if trigger_mode == "external":
            external_mode = str(self._external_mode_combo.currentData() or "sequential")
            if external_mode not in {"sequential", "explicit"}:
                external_mode = "sequential"
            if external_mode == "explicit":
                selected_input = self._external_input_combo.currentData()
                external_input = int(selected_input) if selected_input is not None else None

        profile = self._build_camera_profile(exposure_us, gain_db, gamma, brightness, sharpness)
        if trigger_gap_ms is None:
            resolution = self._selected_resolution_data()
            trigger_gap_ms = get_default_trigger_gap_ms(
                resolution.get("width"),
                resolution.get("height"),
                resolution.get("fps"),
            )

        self._result = {
            "name": name,
            "camera_profile": profile,
            "settle_ms": settle_ms,
            "trigger_mode": trigger_mode,
            "external_trigger_mode": external_mode,
            "external_request_input": external_input,
            "trigger_interval_ms": interval_ms,
            "trigger_gap_ms": trigger_gap_ms,
            "frame_source_view_id": self._frame_source_combo.currentData(),
            "image_rotation": int(self._rotation_combo.currentData() or 0),
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
            idx = self._resolution_combo.count() - 1
            self._apply_resolution_warning_style(idx, current_camera)

        for label, data in self._available_resolutions:
            self._resolution_combo.addItem(label, dict(data))
            idx = self._resolution_combo.count() - 1
            self._apply_resolution_warning_style(idx, data)

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
                    idx = self._resolution_combo.count() - 1
                    self._apply_resolution_warning_style(idx, target)
                    self._resolution_combo.setCurrentIndex(idx)

    @staticmethod
    def _resolution_warning_note(data: dict[str, Any]) -> Optional[str]:
        key = (
            int(data.get("width", 0) or 0),
            int(data.get("height", 0) or 0),
            int(data.get("fps", 0) or 0),
            str(data.get("pixel_format", "") or "").upper(),
        )
        return _TRIGGER_RESOLUTION_WARNINGS.get(key)

    def _apply_resolution_warning_style(self, index: int, data: dict[str, Any]) -> None:
        note = self._resolution_warning_note(data)
        if not note:
            return
        self._resolution_combo.setItemData(index, QBrush(QColor("#d12b2b")), Qt.ForegroundRole)
        self._resolution_combo.setItemData(index, note, Qt.ToolTipRole)

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
            if profile_obj.flash_mode is not None:
                index = self._flash_mode_combo.findData(profile_obj.flash_mode)
                if index >= 0:
                    self._flash_mode_combo.setCurrentIndex(index)

    def _apply_initial_timing(
        self,
        settle_ms: Optional[int],
        trigger_mode: str,
        external_trigger_mode: Optional[str],
        external_request_input: Optional[int],
        trigger_interval_ms: Optional[int],
        trigger_gap_ms: Optional[float],
    ) -> None:
        if settle_ms is not None:
            self._settle_edit.setText(str(int(settle_ms)))

        normalized_mode = str(trigger_mode or "timed").strip().lower()
        if normalized_mode not in {"timed", "external", "manual"}:
            normalized_mode = "timed"
        index = self._trigger_mode_combo.findData(normalized_mode)
        if index >= 0:
            self._trigger_mode_combo.setCurrentIndex(index)

        normalized_external_mode = str(external_trigger_mode or "").strip().lower()
        if normalized_mode != "external":
            normalized_external_mode = "sequential"
        elif normalized_external_mode not in {"sequential", "explicit"}:
            normalized_external_mode = "sequential"
        external_mode_idx = self._external_mode_combo.findData(normalized_external_mode)
        if external_mode_idx >= 0:
            self._external_mode_combo.setCurrentIndex(external_mode_idx)

        if external_request_input in {1, 2, 3, 4, 5, 6, 7, 8}:
            external_input_idx = self._external_input_combo.findData(int(external_request_input))
            if external_input_idx >= 0:
                self._external_input_combo.setCurrentIndex(external_input_idx)

        if trigger_interval_ms is not None:
            self._interval_edit.setText(str(int(trigger_interval_ms)))
        if trigger_gap_ms is not None:
            self._trigger_exposure_edit.setText(str(float(trigger_gap_ms)).rstrip("0").rstrip("."))
        self._on_trigger_mode_changed()
        self._on_external_mode_changed()
        self._update_trigger_gap_warning()

    def _apply_initial_rotation(self, image_rotation: int) -> None:
        try:
            rotation = int(image_rotation)
        except Exception:
            rotation = 0
        if rotation not in {0, 90, 180, 270}:
            rotation = 0
        self._image_rotation = rotation
        index = self._rotation_combo.findData(rotation)
        if index >= 0:
            self._rotation_combo.setCurrentIndex(index)

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
        external_active = mode == "external"
        self._external_mode_combo.setEnabled(external_active)
        self._external_mode_label.setVisible(external_active)
        self._external_mode_combo.setVisible(external_active)
        if not external_active:
            ext_mode_idx = self._external_mode_combo.findData("sequential")
            if ext_mode_idx >= 0:
                self._external_mode_combo.setCurrentIndex(ext_mode_idx)
        self._on_external_mode_changed()

    def _on_external_mode_changed(self) -> None:
        trigger_mode = str(self._trigger_mode_combo.currentData() or "timed")
        external_mode = str(self._external_mode_combo.currentData() or "sequential")
        explicit_active = trigger_mode == "external" and external_mode == "explicit"
        self._external_input_combo.setEnabled(explicit_active)
        self._external_input_label.setVisible(trigger_mode == "external")
        self._external_input_combo.setVisible(trigger_mode == "external")
        if not explicit_active:
            unused_idx = self._external_input_combo.findData(None)
            if unused_idx >= 0:
                self._external_input_combo.setCurrentIndex(unused_idx)

    def _selected_resolution_data(self) -> dict[str, Any]:
        resolution_data = self._resolution_combo.currentData()
        if isinstance(resolution_data, dict):
            return dict(resolution_data)
        current_text = self._resolution_combo.currentText()
        for label, data in self._available_resolutions:
            if label == current_text:
                return dict(data)
        return {}

    def _update_trigger_gap_warning(self) -> None:
        resolution = self._selected_resolution_data()
        min_period_ms = get_trigger_min_period_ms(
            resolution.get("width"),
            resolution.get("height"),
            resolution.get("fps"),
        )
        try:
            gap_ms = self._parse_optional_float(self._trigger_exposure_edit.text())
        except ValueError:
            self._trigger_warning_label.setVisible(False)
            return
        if gap_ms is None:
            self._trigger_warning_label.setVisible(False)
            return
        effective_period_ms = float(self._trigger_pulse_ms) + float(gap_ms)
        if effective_period_ms < float(min_period_ms):
            self._trigger_warning_label.setText(
                "Nízka expozícia v trigger mode môže spôsobiť banding alebo nerovnomernú expozíciu."
            )
            self._trigger_warning_label.setVisible(True)
            return
        self._trigger_warning_label.setVisible(False)

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

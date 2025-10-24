"""Panel for managing multi-view recipe steps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(slots=True)
class StepDescriptor:
    step_id: str
    name: str
    order: int
    pose_enabled: bool
    settle_ms: Optional[int]
    camera_profile: Dict[str, Any]


class StepListWidget(QListWidget):
    stepsReordered = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, event):  # type: ignore[override]
        super().dropEvent(event)
        step_ids: list[str] = []
        for row in range(self.count()):
            item = self.item(row)
            if item is None:
                continue
            step_id = item.data(Qt.UserRole)
            if step_id:
                step_ids.append(str(step_id))
        if step_ids:
            self.stepsReordered.emit(step_ids)


class MultiViewPanel(QWidget):
    """UI panel that exposes multi-view step management controls."""

    stepSelected = Signal(str)
    addStepRequested = Signal()
    removeStepRequested = Signal(str)
    captureGoldenRequested = Signal(str)
    loadGoldenRequested = Signal(str)
    saveStepRequested = Signal(str, dict)
    sequenceTestRequested = Signal()
    stepsReordered = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._steps: MutableMapping[str, StepDescriptor] = {}
        self._steps_order: list[str] = []
        self._current_step_id: Optional[str] = None
        self._loading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # --- Steps list ---
        list_container = QVBoxLayout()
        list_container.setContentsMargins(0, 0, 0, 0)
        list_container.setSpacing(6)

        self._step_list = StepListWidget(self)
        self._step_list.currentItemChanged.connect(self._on_step_selection_changed)
        self._step_list.stepsReordered.connect(self.stepsReordered.emit)
        list_container.addWidget(self._step_list, 1)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(6)
        self._btn_add = QPushButton("Add step", self)
        self._btn_remove = QPushButton("Remove step", self)
        self._btn_add.clicked.connect(self.addStepRequested.emit)
        self._btn_remove.clicked.connect(self._on_remove_clicked)
        buttons_row.addWidget(self._btn_add)
        buttons_row.addWidget(self._btn_remove)
        list_container.addLayout(buttons_row)

        layout.addLayout(list_container, 0)

        # --- Details panel ---
        self._details_container = QVBoxLayout()
        self._details_container.setContentsMargins(0, 0, 0, 0)
        self._details_container.setSpacing(8)

        self._step_id_label = QLabel("ID: —", self)
        self._step_name_edit = QLineEdit(self)
        self._step_name_edit.setPlaceholderText("View name")
        self._step_name_edit.textEdited.connect(self._on_name_edited)

        self._pose_checkbox = QCheckBox("Enable pose alignment", self)

        self._settle_spin = QSpinBox(self)
        self._settle_spin.setRange(0, 60000)
        self._settle_spin.setSpecialValueText("Auto")
        self._settle_spin.setSuffix(" ms")

        general_form = QFormLayout()
        general_form.setContentsMargins(0, 0, 0, 0)
        general_form.setSpacing(6)
        general_form.addRow(self._step_id_label)
        general_form.addRow("Name", self._step_name_edit)
        general_form.addRow("Pose alignment", self._pose_checkbox)
        general_form.addRow("Settle", self._settle_spin)

        self._details_container.addLayout(general_form)

        # Camera group
        camera_group = QGroupBox("Camera profile", self)
        camera_form = QFormLayout(camera_group)
        camera_form.setContentsMargins(8, 8, 8, 8)
        camera_form.setSpacing(6)

        self._resolution_w = QSpinBox(self)
        self._resolution_w.setRange(0, 8192)
        self._resolution_w.setSpecialValueText("Auto")
        self._resolution_h = QSpinBox(self)
        self._resolution_h.setRange(0, 8192)
        self._resolution_h.setSpecialValueText("Auto")
        resolution_row = QHBoxLayout()
        resolution_row.setContentsMargins(0, 0, 0, 0)
        resolution_row.setSpacing(6)
        resolution_row.addWidget(self._resolution_w, 1)
        resolution_row.addWidget(QLabel("×", self))
        resolution_row.addWidget(self._resolution_h, 1)
        resolution_widget = QWidget(self)
        resolution_widget.setLayout(resolution_row)

        self._fps_spin = QDoubleSpinBox(self)
        self._fps_spin.setRange(0.0, 240.0)
        self._fps_spin.setDecimals(3)
        self._fps_spin.setSingleStep(1.0)
        self._fps_spin.setSpecialValueText("Auto")

        self._exposure_spin = QDoubleSpinBox(self)
        self._exposure_spin.setRange(0.0, 10000.0)
        self._exposure_spin.setDecimals(3)
        self._exposure_spin.setSingleStep(0.5)
        self._exposure_spin.setSpecialValueText("Auto")

        self._gain_spin = QDoubleSpinBox(self)
        self._gain_spin.setRange(0.0, 1000.0)
        self._gain_spin.setDecimals(3)
        self._gain_spin.setSingleStep(0.5)
        self._gain_spin.setSpecialValueText("Auto")

        self._pixel_format_edit = QLineEdit(self)
        self._pixel_format_edit.setPlaceholderText("Format string")

        camera_form.addRow("Resolution", resolution_widget)
        camera_form.addRow("FPS", self._fps_spin)
        camera_form.addRow("Exposure", self._exposure_spin)
        camera_form.addRow("Gain", self._gain_spin)
        camera_form.addRow("Pixel format", self._pixel_format_edit)

        self._details_container.addWidget(camera_group)

        # Limits group
        limits_group = QGroupBox("Limits", self)
        limits_layout = QVBoxLayout(limits_group)
        limits_layout.setContentsMargins(8, 8, 8, 8)
        limits_layout.setSpacing(6)

        self._limits_table = QTableWidget(0, 2, self)
        self._limits_table.setHorizontalHeaderLabels(["Key", "Value"])
        header = self._limits_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self._limits_table.verticalHeader().setVisible(False)
        self._limits_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._limits_table.setSelectionMode(QAbstractItemView.SingleSelection)
        limits_layout.addWidget(self._limits_table, 1)

        limits_buttons = QHBoxLayout()
        limits_buttons.setContentsMargins(0, 0, 0, 0)
        limits_buttons.setSpacing(6)
        self._btn_add_limit = QPushButton("Add", self)
        self._btn_remove_limit = QPushButton("Remove", self)
        self._btn_add_limit.clicked.connect(self._on_add_limit)
        self._btn_remove_limit.clicked.connect(self._on_remove_limit)
        limits_buttons.addWidget(self._btn_add_limit)
        limits_buttons.addWidget(self._btn_remove_limit)
        limits_layout.addLayout(limits_buttons)

        self._details_container.addWidget(limits_group, 1)

        # Golden controls
        golden_row = QHBoxLayout()
        golden_row.setContentsMargins(0, 0, 0, 0)
        golden_row.setSpacing(6)
        self._btn_capture = QPushButton("Capture golden", self)
        self._btn_load = QPushButton("Load golden", self)
        self._btn_save = QPushButton("Save step", self)
        self._btn_test_sequence = QPushButton("Test sequence", self)
        self._btn_capture.clicked.connect(self._on_capture_clicked)
        self._btn_load.clicked.connect(self._on_load_clicked)
        self._btn_save.clicked.connect(self._on_save_clicked)
        self._btn_test_sequence.clicked.connect(self.sequenceTestRequested.emit)
        golden_row.addWidget(self._btn_capture)
        golden_row.addWidget(self._btn_load)
        golden_row.addWidget(self._btn_save)
        golden_row.addWidget(self._btn_test_sequence)
        self._details_container.addLayout(golden_row)

        self._golden_status = QLabel("Golden: missing", self)
        self._golden_status.setStyleSheet("color: #b03030;")
        self._details_container.addWidget(self._golden_status)

        layout.addLayout(self._details_container, 1)

        self._update_enabled_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_steps(self, steps: Sequence[Mapping[str, Any]]) -> None:
        self._steps.clear()
        self._steps_order = []
        self._step_list.blockSignals(True)
        self._step_list.clear()
        for index, entry in enumerate(steps):
            step_id = str(entry.get("id") or entry.get("step_id") or f"step-{index + 1}")
            descriptor = StepDescriptor(
                step_id=step_id,
                name=str(entry.get("name") or step_id),
                order=int(entry.get("order", index)),
                pose_enabled=bool(entry.get("pose_enabled", True)),
                settle_ms=(
                    None
                    if entry.get("settle_ms") in (None, "")
                    else int(entry.get("settle_ms"))
                ),
                camera_profile=dict(entry.get("camera_profile", {})),
            )
            self._steps[descriptor.step_id] = descriptor
            self._steps_order.append(descriptor.step_id)
            item = QListWidgetItem(f"{index + 1}. {descriptor.name}")
            item.setData(Qt.UserRole, descriptor.step_id)
            self._step_list.addItem(item)
        self._step_list.blockSignals(False)
        self._update_enabled_state()

    def select_step(self, step_id: str) -> None:
        if not step_id:
            return
        for row in range(self._step_list.count()):
            item = self._step_list.item(row)
            if item and item.data(Qt.UserRole) == step_id:
                self._step_list.setCurrentRow(row)
                return

    def set_step_details(
        self,
        step_id: str,
        *,
        name: str,
        pose_enabled: bool,
        settle_ms: Optional[int],
        camera_profile: Mapping[str, Any],
    ) -> None:
        self._loading = True
        try:
            self._current_step_id = step_id
            self._step_id_label.setText(f"ID: {step_id}")
            self._step_name_edit.setText(name)
            self._pose_checkbox.setChecked(bool(pose_enabled))
            if settle_ms is None or settle_ms <= 0:
                self._settle_spin.setValue(0)
            else:
                self._settle_spin.setValue(int(settle_ms))

            width, height = 0, 0
            if isinstance(camera_profile.get("resolution"), (list, tuple)) and len(camera_profile["resolution"]) >= 2:
                try:
                    width = int(camera_profile["resolution"][0])
                    height = int(camera_profile["resolution"][1])
                except Exception:
                    width, height = 0, 0
            self._resolution_w.setValue(max(0, width))
            self._resolution_h.setValue(max(0, height))

            def _to_float(key: str) -> float:
                value = camera_profile.get(key)
                if value in (None, ""):
                    return 0.0
                try:
                    return float(value)
                except Exception:
                    return 0.0

            self._fps_spin.setValue(_to_float("fps"))
            self._exposure_spin.setValue(_to_float("exposure"))
            self._gain_spin.setValue(_to_float("gain"))
            self._pixel_format_edit.setText(str(camera_profile.get("pixel_format") or ""))
        finally:
            self._loading = False
        self._update_enabled_state()
        self._refresh_list_labels()

    def set_limits(self, limits: Mapping[str, Any]) -> None:
        self._limits_table.setRowCount(0)
        for key, value in dict(limits or {}).items():
            row = self._limits_table.rowCount()
            self._limits_table.insertRow(row)
            key_item = QTableWidgetItem(str(key))
            val_item = QTableWidgetItem("" if value is None else str(value))
            self._limits_table.setItem(row, 0, key_item)
            self._limits_table.setItem(row, 1, val_item)

    def set_golden_status(self, available: bool) -> None:
        if available:
            self._golden_status.setText("Golden: available")
            self._golden_status.setStyleSheet("color: #2f8f2f;")
        else:
            self._golden_status.setText("Golden: missing")
            self._golden_status.setStyleSheet("color: #b03030;")

    def update_step_name(self, step_id: str, name: str) -> None:
        descriptor = self._steps.get(step_id)
        if descriptor is not None:
            descriptor.name = name
        self._refresh_list_labels()

    def clear(self) -> None:
        self._steps.clear()
        self._steps_order.clear()
        self._step_list.clear()
        self._current_step_id = None
        self._step_id_label.setText("ID: —")
        self._step_name_edit.clear()
        self._pose_checkbox.setChecked(True)
        self._settle_spin.setValue(0)
        self._resolution_w.setValue(0)
        self._resolution_h.setValue(0)
        self._fps_spin.setValue(0.0)
        self._exposure_spin.setValue(0.0)
        self._gain_spin.setValue(0.0)
        self._pixel_format_edit.clear()
        self._limits_table.setRowCount(0)
        self.set_golden_status(False)
        self._update_enabled_state()

    def current_step_form(self) -> Optional[dict[str, Any]]:
        step_id = self._current_step_id
        if not step_id:
            return None
        camera_profile: Dict[str, Any] = {}
        width = self._resolution_w.value()
        height = self._resolution_h.value()
        if width > 0 and height > 0:
            camera_profile["resolution"] = [int(width), int(height)]
        fps = self._fps_spin.value()
        if fps > 0:
            camera_profile["fps"] = float(fps)
        exposure = self._exposure_spin.value()
        if exposure > 0:
            camera_profile["exposure"] = float(exposure)
        gain = self._gain_spin.value()
        if gain > 0:
            camera_profile["gain"] = float(gain)
        pixel_format = self._pixel_format_edit.text().strip()
        if pixel_format:
            camera_profile["pixel_format"] = pixel_format

        limits: Dict[str, Any] = {}
        for row in range(self._limits_table.rowCount()):
            key_item = self._limits_table.item(row, 0)
            val_item = self._limits_table.item(row, 1)
            if key_item is None:
                continue
            key = key_item.text().strip()
            if not key:
                continue
            value_text = val_item.text().strip() if val_item else ""
            if not value_text:
                limits[key] = None
                continue
            try:
                limits[key] = float(value_text)
            except Exception:
                limits[key] = value_text

        settle = self._settle_spin.value()
        form_data = {
            "id": step_id,
            "name": self._step_name_edit.text().strip() or step_id,
            "pose_enabled": self._pose_checkbox.isChecked(),
            "settle_ms": None if settle <= 0 else int(settle),
            "camera_profile": camera_profile,
            "limits": limits,
        }
        return form_data

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_step_selection_changed(self, current: QListWidgetItem, _previous: QListWidgetItem) -> None:
        step_id = current.data(Qt.UserRole) if current is not None else None
        self._current_step_id = str(step_id) if step_id else None
        self._update_enabled_state()
        if self._current_step_id:
            self.stepSelected.emit(self._current_step_id)

    def _on_remove_clicked(self) -> None:
        step_id = self._current_step_id
        if not step_id:
            return
        answer = QMessageBox.question(
            self,
            "Remove step",
            "Remove the selected step from the multi-view recipe?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.removeStepRequested.emit(step_id)

    def _on_add_limit(self) -> None:
        row = self._limits_table.rowCount()
        self._limits_table.insertRow(row)
        self._limits_table.setItem(row, 0, QTableWidgetItem("limit_key"))
        self._limits_table.setItem(row, 1, QTableWidgetItem("0"))

    def _on_remove_limit(self) -> None:
        rows = {index.row() for index in self._limits_table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._limits_table.removeRow(row)

    def _on_capture_clicked(self) -> None:
        if self._current_step_id:
            self.captureGoldenRequested.emit(self._current_step_id)

    def _on_load_clicked(self) -> None:
        if self._current_step_id:
            self.loadGoldenRequested.emit(self._current_step_id)

    def _on_save_clicked(self) -> None:
        data = self.current_step_form()
        if not data:
            return
        self.saveStepRequested.emit(data["id"], data)

    def _on_name_edited(self, text: str) -> None:
        if self._loading or not self._current_step_id:
            return
        descriptor = self._steps.get(self._current_step_id)
        if descriptor is not None:
            descriptor.name = text.strip() or descriptor.step_id
        self._refresh_list_labels()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _update_enabled_state(self) -> None:
        has_steps = self._step_list.count() > 0
        has_selection = bool(self._current_step_id)
        for widget in (
            self._step_name_edit,
            self._pose_checkbox,
            self._settle_spin,
            self._resolution_w,
            self._resolution_h,
            self._fps_spin,
            self._exposure_spin,
            self._gain_spin,
            self._pixel_format_edit,
            self._limits_table,
            self._btn_add_limit,
            self._btn_remove_limit,
            self._btn_capture,
            self._btn_load,
            self._btn_save,
            self._btn_test_sequence,
        ):
            widget.setEnabled(has_selection)
        self._btn_remove.setEnabled(has_selection)
        if not has_steps:
            self._step_id_label.setText("ID: —")
            self._golden_status.setText("Golden: missing")
            self._golden_status.setStyleSheet("color: #b03030;")

    def _refresh_list_labels(self) -> None:
        for index in range(self._step_list.count()):
            item = self._step_list.item(index)
            if item is None:
                continue
            step_id = item.data(Qt.UserRole)
            descriptor = self._steps.get(step_id)
            name = descriptor.name if descriptor else str(step_id)
            item.setText(f"{index + 1}. {name}")


__all__ = ["MultiViewPanel"]


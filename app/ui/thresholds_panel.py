# app/ui/thresholds_panel.py
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.schema import Tool, ToolParams, ToolThresholds
from app.ui.golden_wizard import ToolConfigPanel


class ThresholdsPanel(QWidget):
    """Per-tool editor for parameters and thresholds in the active recipe."""

    def __init__(self, mw):
        super().__init__(mw)

        self.mw = mw
        self._tools: List[Tool] = []
        self._current_index: int = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(0, 0, 0, 0)
        selector_row.setSpacing(6)

        selector_row.addWidget(QLabel("Nástroj:", self))

        self._tool_combo = QComboBox(self)
        self._tool_combo.currentIndexChanged.connect(self._on_tool_selected)
        selector_row.addWidget(self._tool_combo, 1)

        self._refresh_button = QPushButton("Obnoviť", self)
        self._refresh_button.clicked.connect(self.refresh_from_tool)
        selector_row.addWidget(self._refresh_button)

        layout.addLayout(selector_row)

        self._empty_label = QLabel(
            "V recepte zatiaľ nie sú žiadne nástroje na úpravu.",
            self,
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self._empty_label)

        self._tool_panel = ToolConfigPanel(self)
        self._tool_panel.paramChanged.connect(self._on_param_changed)
        self._tool_panel.thresholdChanged.connect(self._on_threshold_changed)
        self._tool_panel.locatorPolicyWarningChanged.connect(
            self._on_policy_warning_changed
        )

        # skry testovanie a diagnostiku – v hlavnom okne nespúšťame tool testy
        if hasattr(self._tool_panel, "_btn_test"):
            self._tool_panel._btn_test.setVisible(False)
        if hasattr(self._tool_panel, "_test_result_label"):
            self._tool_panel._test_result_label.setVisible(False)
        if hasattr(self._tool_panel, "_diagnostics_group"):
            self._tool_panel._diagnostics_group.setVisible(False)

        layout.addWidget(self._tool_panel, 1)

        self._policy_label = QLabel("", self)
        self._policy_label.setStyleSheet("color: #b36b00; font-size: 11px;")
        self._policy_label.setWordWrap(True)
        self._policy_label.setVisible(False)
        layout.addWidget(self._policy_label)

        self.refresh_from_tool()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def refresh_from_tool(self) -> None:
        """Reload tools for the currently selected recipe."""

        recipe = self.mw.current_recipe_name()
        try:
            # načítaj draft, aby boli k dispozícii najnovšie úpravy z wizardu
            self.mw.recipes.load_tools(recipe, use_draft=True)
        except Exception:
            # defensive – ak recept ešte nemá tools.json
            pass

        self._tools = list(self.mw.recipes.get_draft_tools(recipe))
        self._tool_combo.blockSignals(True)
        self._tool_combo.clear()
        for tool in self._tools:
            self._tool_combo.addItem(f"{tool.name} ({tool.type})")
        self._tool_combo.blockSignals(False)

        has_tools = bool(self._tools)
        self._tool_combo.setEnabled(has_tools)
        self._refresh_button.setEnabled(True)

        if not has_tools:
            self._current_index = -1
            self._tool_panel.clear()
            self._tool_panel.setVisible(False)
            self._empty_label.setText(
                "V recepte zatiaľ nie sú žiadne nástroje na úpravu."
            )
            self._empty_label.setVisible(True)
            self._policy_label.clear()
            self._policy_label.setVisible(False)
            return

        self._empty_label.setVisible(False)
        self._tool_panel.setVisible(True)

        if 0 <= self._current_index < len(self._tools):
            target_index = self._current_index
        else:
            target_index = 0

        self._tool_combo.setCurrentIndex(target_index)
        self._on_tool_selected(target_index)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_tool_selected(self, index: int) -> None:
        if index < 0 or index >= len(self._tools):
            self._current_index = -1
            self._tool_panel.clear()
            self._tool_panel.setVisible(False)
            self._empty_label.setVisible(True)
            return

        tool = self._tools[index]
        try:
            meta = self.mw.recipes.tool.get_tool_meta(tool.type)
            schema = self.mw.recipes.tool.get_tool_schema(tool.type)
        except Exception as exc:
            self._current_index = -1
            self._tool_panel.clear()
            self._tool_panel.setVisible(False)
            self._empty_label.setText(
                f"Metadata pre nástroj {tool.type} sa nepodarilo načítať: {exc}"
            )
            self._empty_label.setVisible(True)
            return

        self._current_index = index
        self._empty_label.setVisible(False)
        self._tool_panel.setVisible(True)

        policy = self.mw.recipes.get_locator_failure_policy(
            self.mw.current_recipe_name()
        )
        self._tool_panel.set_locator_failure_policy(policy)
        self._tool_panel.set_tool(tool, meta, schema)
        self._tool_panel.refresh_values(tool)

    def _on_param_changed(self, name: str, value) -> None:
        index = self._current_index
        if index < 0 or index >= len(self._tools):
            return

        recipe = self.mw.current_recipe_name()
        tool = self._tools[index]
        params = dict(getattr(tool.params, "values", {}) or {})
        params[name] = value
        tool.params = ToolParams(params)

        try:
            updated = self.mw.recipes.update_tool(recipe, index, tool)
        except Exception as exc:
            QMessageBox.critical(self, "Uloženie parametra zlyhalo", str(exc))
            self.refresh_from_tool()
            return

        self._tools = list(updated)
        self._tool_panel.refresh_values(self._tools[index])

    def _on_threshold_changed(self, name: str, value) -> None:
        index = self._current_index
        if index < 0 or index >= len(self._tools):
            return

        recipe = self.mw.current_recipe_name()
        tool = self._tools[index]
        thresholds = dict(getattr(tool.thresholds, "values", {}) or {})
        thresholds[name] = value
        tool.thresholds = ToolThresholds(thresholds)

        try:
            updated = self.mw.recipes.update_tool(recipe, index, tool)
        except Exception as exc:
            QMessageBox.critical(self, "Uloženie thresholdu zlyhalo", str(exc))
            self.refresh_from_tool()
            return

        self._tools = list(updated)
        self._tool_panel.refresh_values(self._tools[index])

    def _on_policy_warning_changed(self, message: str) -> None:
        text = (message or "").strip()
        self._policy_label.setText(text)
        self._policy_label.setVisible(bool(text))


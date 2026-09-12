"""Metadata-driven Tool Catalog for the Golden Wizard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from app.ui.golden_wizard.style import TOOL_CATALOG_STYLE

_ALL = "__all__"
_RECOMMENDED = "__recommended__"
_RECOMMENDED_TYPES = {
    "locator.template_match", "presence_absence", "presence.absence_v2",
    "ssim", "edge_change", "edge_profile_deviation",
}
_NAME_SK = {
    "locator.template_match": "Vyhľadanie a zarovnanie vzoru",
    "mold.protection_v1": "Kontrola prázdnej formy",
    "presence_absence": "Prítomnosť podľa svetlej/tmavej plochy",
    "presence.absence_v2": "Naučená kontrola odchýlok (V2)",
    "ssim": "Podobnosť vzhľadu (SSIM)",
    "edge_change": "Plocha rozdielov oproti referencii",
    "edge_profile_deviation": "Odchýlka profilu hrany",
    "light_presence": "Otvor – prednastavenie prítomnosti",
    "light_transmission": "Kontrola priepustnosti svetla",
    "mse": "Rozdiel jasu (MSE)", "ncc": "Podobnosť vzoru (NCC)",
}
_CATEGORY_SK = {
    "locator": "Základné", "presence": "Základné", "similarity": "Základné",
    "change detection": "Základné", "edge": "Základné", "measurement": "Základné",
}
_CATEGORY_ORDER = ["Základné", "Prednastavenia", "Špecializované", "Pokročilé", "Ostatné"]
_CATEGORY_BY_TYPE = {
    "mse": "Pokročilé", "ncc": "Pokročilé", "light_presence": "Prednastavenia",
    "mold.protection_v1": "Špecializované", "light_transmission": "Špecializované",
}
_HIDDEN_NEW_TYPES = {"absdiff", "ssd", "template_match"}


def make_catalog_tool(service, type_id):
    """New-tool presets only; never rewrite a saved legacy tool."""
    tool = service.make_default_tool("presence_absence" if type_id == "light_presence" else type_id)
    if type_id == "light_presence":
        tool.name = "Kontrola otvoru"
        tool.params.values.update(polarity="bright", binary_threshold=200, gaussian_blur_kernel=0)
        tool.thresholds.values.update(min_area_px=100, max_area_px=10000, min_fill_ratio=0.0, max_fill_ratio=1.0)
    return tool


@dataclass(frozen=True)
class _CatalogEntry:
    type_id: str
    name: str
    description: str
    category_label: str
    supports_roi: bool
    supports_mask: bool
    deprecated: bool
    metrics: tuple[str, ...]


class ToolCard(QFrame):
    clicked = Signal(str)
    doubleClicked = Signal(str)

    def __init__(self, entry: _CatalogEntry, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("toolCard")
        self.setProperty("selected", False)
        self.setProperty("deprecated", entry.deprecated)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(entry.type_id)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        title_row = QHBoxLayout()
        title = QLabel(entry.name, self)
        title.setWordWrap(True)
        title.setProperty("role", "cardTitle")
        title_row.addWidget(title, 1)
        if entry.deprecated:
            badge = QLabel("Zastaraný", self)
            badge.setProperty("role", "warningBadge")
            title_row.addWidget(badge)
        layout.addLayout(title_row)
        description = QLabel(entry.description or "Popis nástroja nie je dostupný.", self)
        description.setWordWrap(True)
        description.setToolTip(entry.description)
        description.setMaximumHeight(48)
        description.setProperty("role", "secondary")
        layout.addWidget(description)
        capabilities = QLabel(
            f"ROI: {'áno' if entry.supports_roi else 'nie'}    "
            f"Ignorovacia maska: {'áno' if entry.supports_mask else 'nie'}", self,
        )
        capabilities.setProperty("role", "capabilities")
        layout.addWidget(capabilities)
        if entry.metrics:
            output = QLabel(f"Výstup: {', '.join(entry.metrics)}", self)
            output.setProperty("role", "secondary")
            output.setWordWrap(True)
            layout.addWidget(output)
        technical = QLabel(entry.type_id, self)
        technical.setProperty("role", "technical")
        layout.addWidget(technical)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.entry.type_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.entry.type_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ToolCatalogDialog(QDialog):
    """Two-panel catalog preserving the existing selected_type contract."""

    def __init__(self, tool_service, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("toolCatalog")
        self.setStyleSheet(TOOL_CATALOG_STYLE)
        self.setWindowTitle("Pridať nástroj")
        self.setModal(True)
        self.resize(1100, 700)
        self._tool_service = tool_service
        self._selected_type: str | None = None
        self._cards: dict[str, ToolCard] = {}
        self._entries = self._load_entries()

        title = QLabel("Pridať nástroj", self)
        title.setProperty("role", "dialogTitle")
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Hľadať nástroj...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._search)

        self._categories = QListWidget(self)
        self._categories.setObjectName("categoryList")
        self._categories.setMinimumWidth(190)
        self._categories.setMaximumWidth(260)
        self._categories.setSelectionMode(QAbstractItemView.SingleSelection)
        self._populate_categories()
        category_panel = QFrame(self)
        category_panel.setObjectName("catalogPanel")
        category_layout = QVBoxLayout(category_panel)
        category_heading = QLabel("KATEGÓRIE", category_panel)
        category_heading.setProperty("role", "panelHeader")
        category_layout.addWidget(category_heading)
        category_layout.addWidget(self._categories, 1)

        self._tools = QListWidget(self)
        self._tools.setObjectName("toolCards")
        self._tools.setSelectionMode(QAbstractItemView.NoSelection)
        self._tools.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._empty = QLabel(
            "Nenašli sa žiadne nástroje.\nSkúste inú kategóriu alebo vyhľadávanie.", self
        )
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setProperty("role", "emptyState")
        tools_panel = QFrame(self)
        tools_panel.setObjectName("catalogPanel")
        tools_layout = QVBoxLayout(tools_panel)
        tools_heading = QLabel("NÁSTROJE", tools_panel)
        tools_heading.setProperty("role", "panelHeader")
        tools_layout.addWidget(tools_heading)
        tools_layout.addWidget(self._tools, 1)
        tools_layout.addWidget(self._empty, 1)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(category_panel)
        splitter.addWidget(tools_panel)
        splitter.setSizes([220, 820])
        splitter.setStretchFactor(1, 1)

        cancel = QPushButton("Zrušiť", self)
        cancel.clicked.connect(self.reject)
        self._add_button = QPushButton("Pridať nástroj", self)
        self._add_button.setProperty("role", "primary")
        self._add_button.setEnabled(False)
        self._add_button.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(self._add_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        layout.addLayout(actions)
        self._categories.currentItemChanged.connect(lambda *_: self._apply_filter())
        self._apply_filter()

    def _load_entries(self) -> list[_CatalogEntry]:
        entries: list[_CatalogEntry] = []
        for type_id in self._tool_service.list_tool_types():
            if type_id in _HIDDEN_NEW_TYPES:
                continue
            try:
                definition = self._tool_service.get_tool_meta(type_id)
            except KeyError:
                continue
            raw_category = str(getattr(definition, "category", "General") or "General")
            capabilities = getattr(definition, "meta", None)
            metrics = tuple(
                str(metric.description or metric.key)
                for metric in getattr(definition, "metrics_spec", ()) if metric.key
            )
            entries.append(_CatalogEntry(
                type_id=type_id,
                name=_NAME_SK.get(type_id, str(getattr(definition, "name", type_id))),
                description=("Prednastaví jednoduchú kontrolu svetlej plochy pre presvietený otvor. Používa nástroj Prítomnosť podľa plochy." if type_id == "light_presence" else str(getattr(definition, "description", "") or "")),
                category_label=_CATEGORY_BY_TYPE.get(type_id, "Základné" if type_id in _RECOMMENDED_TYPES else self._category_label(raw_category)),
                supports_roi=bool(getattr(capabilities, "supports_roi", False)),
                supports_mask=bool(getattr(capabilities, "supports_ignore_mask", False)),
                deprecated=bool(getattr(definition, "deprecated", False)),
                metrics=metrics[:5],
            ))
        return sorted(entries, key=lambda entry: (entry.category_label, entry.name.lower()))

    @staticmethod
    def _category_label(raw_category: str) -> str:
        normalized = raw_category.strip().lower()
        if normalized in _CATEGORY_SK:
            return _CATEGORY_SK[normalized]
        for key, label in _CATEGORY_SK.items():
            if key in normalized:
                return label
        return raw_category

    def _populate_categories(self) -> None:
        recommended = QListWidgetItem("Odporúčané")
        recommended.setData(Qt.UserRole, _RECOMMENDED)
        self._categories.addItem(recommended)
        labels = {entry.category_label for entry in self._entries}
        ordered = [label for label in _CATEGORY_ORDER if label in labels]
        ordered.extend(sorted(labels.difference(ordered)))
        for label in ordered:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, label)
            self._categories.addItem(item)
        all_tools = QListWidgetItem("Všetky nástroje")
        all_tools.setData(Qt.UserRole, _ALL)
        self._categories.addItem(all_tools)
        self._categories.setCurrentRow(0)

    def _apply_filter(self, _text: str = "") -> None:
        pattern = self._search.text().strip().lower()
        category_item = self._categories.currentItem()
        category = category_item.data(Qt.UserRole) if category_item is not None else _ALL
        previous = self._selected_type
        self._tools.clear()
        self._cards.clear()
        for entry in self._entries:
            if category == _RECOMMENDED and (
                entry.type_id not in _RECOMMENDED_TYPES or entry.deprecated
            ):
                continue
            if category not in (_ALL, _RECOMMENDED) and entry.category_label != category:
                continue
            searchable = " ".join(
                (entry.name, entry.description, entry.category_label, entry.type_id)
            ).lower()
            if pattern and pattern not in searchable:
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(100, 168 if entry.metrics else 138))
            card = ToolCard(entry, self._tools)
            card.clicked.connect(self._select_type)
            card.doubleClicked.connect(self._accept_type)
            self._tools.addItem(item)
            self._tools.setItemWidget(item, card)
            self._cards[entry.type_id] = card
        self._empty.setVisible(self._tools.count() == 0)
        self._tools.setVisible(self._tools.count() > 0)
        self._select_type(previous if previous in self._cards else None)

    def _select_type(self, type_id: Optional[str]) -> None:
        self._selected_type = type_id
        for card_type, card in self._cards.items():
            card.set_selected(card_type == type_id)
        self._add_button.setEnabled(type_id is not None)

    def _accept_type(self, type_id: str) -> None:
        self._select_type(type_id)
        super().accept()

    def accept(self) -> None:  # noqa: N802
        if self._selected_type is None:
            return
        super().accept()

    def selected_type(self) -> str | None:
        return self._selected_type


__all__ = ["ToolCatalogDialog"]

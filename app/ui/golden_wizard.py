# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QMessageBox,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QHeaderView,
    QAbstractItemView,
)

import os
from pathlib import Path
from typing import Optional

import numpy as np

from app.ui.draw_view import DrawView, RoiMaskEditor
from app.services.storage_service import save_golden, save_validation_image
from app.models.regions import Region, validate_cardinality
from app.services.live_preview_service import LivePreviewService
from app.models.schema import RecipeData, Tool, ToolMask, ToolRoi
from app.services.recipe_service import RecipeService


class ToolCatalogDialog(QDialog):
    def __init__(self, tool_service, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tool catalog")
        self._tool_service = tool_service
        self._selected_type: str | None = None

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter tools…")
        self._filter.textChanged.connect(self._apply_filter)

        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._filter)
        layout.addWidget(self._list)
        layout.addWidget(buttons)

        self._entries: list[tuple[str, str, str]] = []
        self._populate_entries()
        self._apply_filter("")

    def _populate_entries(self) -> None:
        self._entries.clear()
        for tool_type in self._tool_service.list_tool_types():
            try:
                meta = self._tool_service.get_tool_meta(tool_type)
                display = f"{meta.display_name} ({tool_type})"
                tooltip = meta.description
            except KeyError:
                display = tool_type
                tooltip = tool_type
            self._entries.append((tool_type, display, tooltip))

    def _apply_filter(self, text: str) -> None:
        pattern = (text or "").strip().lower()
        self._list.clear()
        for tool_type, display, tooltip in self._entries:
            if pattern and pattern not in display.lower() and pattern not in tool_type.lower():
                continue
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, tool_type)
            item.setToolTip(tooltip)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        self._selected_type = item.data(Qt.UserRole)
        super().accept()

    def accept(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        self._selected_type = current.data(Qt.UserRole)
        super().accept()

    def selected_type(self) -> str | None:
        return self._selected_type


class ToolEditDialog(QDialog):
    """Dialog providing ROI and ignore mask editing for a tool."""

    def __init__(self, tool: Tool, golden_image: Optional[np.ndarray], meta, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Edit Tool – {tool.name}")
        self._tool = tool.copy()
        self._meta = meta

        self._editor = RoiMaskEditor(self)
        self._editor.set_roi_enabled(getattr(meta, "supports_roi", True))
        self._editor.set_mask_enabled(getattr(meta, "supports_ignore_mask", True))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QLabel(f"{tool.name} ({tool.type})", self)
        header.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(header)

        layout.addWidget(self._editor, 1)

        self._info_label = QLabel("", self)
        self._info_label.setStyleSheet("color: #666;")
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        pixmap = self._pixmap_from_array(golden_image)
        if pixmap is not None:
            self._editor.set_background(pixmap)
            self._info_label.setText("ROI mode selects the inspection window. Mask mode ignores painted pixels.")
            if getattr(meta, "supports_roi", True):
                self._editor.set_roi(self._tool.roi.rect())
            if getattr(meta, "supports_ignore_mask", True):
                mask_value = self._tool.ignore_mask.value
                if mask_value is not None:
                    self._editor.set_mask(mask_value)
        else:
            self._info_label.setText("Golden snapshot is not available – editing is disabled.")
            self._editor.setEnabled(False)

        self.resize(900, 640)

    def result_tool(self) -> Tool:
        return self._tool.copy()

    def accept(self) -> None:
        supports_roi = bool(getattr(self._meta, "supports_roi", True))
        supports_mask = bool(getattr(self._meta, "supports_ignore_mask", False))

        if supports_roi:
            roi_rect = self._editor.roi()
            roi = ToolRoi()
            roi.set_rect(roi_rect)
            self._tool.roi = roi
        else:
            self._tool.roi = ToolRoi()

        if supports_mask:
            mask = self._editor.mask()
            if mask is None or not np.any(mask):
                self._tool.ignore_mask = ToolMask(None)
            else:
                self._tool.ignore_mask = ToolMask(mask)
        else:
            self._tool.ignore_mask = ToolMask(None)

        super().accept()

    @staticmethod
    def _pixmap_from_array(img: Optional[np.ndarray]) -> Optional[QPixmap]:
        if img is None:
            return None
        arr = np.asarray(img)
        if arr.size == 0:
            return None
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.ndim != 2:
            arr = np.mean(arr, axis=-1)
        arr_u8 = np.ascontiguousarray(arr.astype(np.uint8))
        height, width = arr_u8.shape
        bytes_per_line = arr_u8.strides[0]
        qimg = QImage(arr_u8.data, width, height, bytes_per_line, QImage.Format_Grayscale8)
        return QPixmap.fromImage(qimg.copy())


class GoldenWizard(QDialog):
    """
    Jediné miesto na nastavenie nástroja:
      1) Získať/načítať GOLDEN (1 ks)
      2) Nakresliť oblasti (Blue pose×1, Green ROI×1, Magenta ignore≤5)
      3) Zbierať validáciu (OK/NOK)
      4) Uložiť recept (golden.png + regions.json)
      5) Live feed (ON/OFF) – samostatný náhľad (bez kreslenia)
    """
    def __init__(self, camera, recipes: RecipeService, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Golden WIZARD")
        self.setModal(True)
        self.cam = camera
        self.recipes = recipes
        self.current_img = None

        # --- Live infra (len video label, bez kreslenia) ---

        dev = os.environ.get("CAM_DEV") or getattr(self.cam, "devices", ["/dev/video0"])[0]
        print(f"[GoldenWizard] Live device: {dev}")
        self._lp = LivePreviewService(dev, 1280, 720, 60)

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)
        self._live_on = False

        # ---- Horná lišta ----
        current_recipe = getattr(self.recipes.tool, "recipe", "default")
        self.recipe_name = QLineEdit(current_recipe, self)
        self.shape_sel   = QComboBox(self); self.shape_sel.addItems(["rect","circle","poly"])
        self.type_sel    = QComboBox(self); self.type_sel.addItems(["pose","roi","ignore"])
        self.chk_pose    = QCheckBox("Použiť globálne zarovnanie (pose alignment)")
        self.chk_pose.setChecked(getattr(self.recipes.tool, "pose_enabled", True))

        self.btn_add_tool = QPushButton("Add tool")
        self.btn_add_tool.clicked.connect(self._open_tool_catalog)

        # Toggle Live
        self.btn_live = QPushButton("Live OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)

        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addWidget(QLabel("Tvar:"));   top.addWidget(self.shape_sel)
        top.addWidget(QLabel("Typ:"));    top.addWidget(self.type_sel)
        top.addStretch(1)
        top.addWidget(self.chk_pose)
        top.addWidget(self.btn_add_tool)
        top.addWidget(self.btn_live)

        # ---- Dva režimy zobrazenia ----
        # 1) Live LABEL (video) – používa sa len pri Live ON
        self.live_lbl = QLabel("—")
        self.live_lbl.setAlignment(Qt.AlignCenter)
        self.live_lbl.setMinimumHeight(360)
        self.live_lbl.hide()  # default skryté

        # 2) DrawView (kreslenie) – používa sa pri Live OFF
        self.view = DrawView(self)
        self.view.set_shape_type(self.shape_sel.currentText())
        self.view.set_region_type(self.type_sel.currentText())

        # ---- Ovládacie tlačidlá ----
        btn_cap_golden   = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden  = QPushButton("Načítať GOLDEN z disku")
        btn_save_recipe  = QPushButton("Uložiť RECEPT")
        btn_val_ok       = QPushButton("Validačný zber: uložiť Ⓞ OK")
        btn_val_nok      = QPushButton("Validačný zber: uložiť ✕ NOK")

        buttons = QHBoxLayout()
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addStretch(1)
        buttons.addWidget(btn_val_ok)
        buttons.addWidget(btn_val_nok)
        buttons.addWidget(btn_save_recipe)

        # ---- Layout ----
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.live_lbl)
        layout.addWidget(self.view)
        self.tools_table = QTableWidget(0, 5, self)
        self.tools_table.setHorizontalHeaderLabels(["Order", "Name", "Type", "Enabled", "Actions"])
        header = self.tools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.tools_table.verticalHeader().setVisible(False)
        self.tools_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tools_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tools_table.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(QLabel("Tools in recipe:"))
        layout.addWidget(self.tools_table)
        layout.addLayout(buttons)

        # signály
        self.shape_sel.currentTextChanged.connect(self.view.set_shape_type)
        self.type_sel.currentTextChanged.connect(self.view.set_region_type)
        btn_cap_golden.clicked.connect(self._capture_golden)
        btn_load_golden.clicked.connect(self._load_golden)
        btn_save_recipe.clicked.connect(self._save_recipe)
        btn_val_ok.clicked.connect(lambda: self._save_validation(True))
        btn_val_nok.clicked.connect(lambda: self._save_validation(False))
        self.recipe_name.editingFinished.connect(self._on_recipe_changed)

        self._last_recipe = self._current_recipe_name()
        try:
            self.recipes.load_tools(self._last_recipe)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {self._last_recipe}: {exc}")
        self._refresh_tools_table()

    # ---------- Live ----------
    def _toggle_live(self, checked: bool):
        if checked:
            try:
                self.cam.pause_for_external()
            except Exception as e:
                print("[GoldenWizard] pause_for_external:", e)


            # Zapnúť live: zobraz label, skryť DrawView (žiadne kreslenie počas live)
            self.view.hide()
            self.live_lbl.show()
            try:
                self._lp.start()
                self._live_timer.start()
                self._live_on = True
                self.btn_live.setText("Live ON")
                # Deaktivuj meniče tvar/typ počas live (čisto vizuálne)
                self.shape_sel.setEnabled(False)
                self.type_sel.setEnabled(False)
            except Exception as e:
                self._err(f"Live feed sa nepodarilo spustiť: {e}")
                self.btn_live.setChecked(False)
                self.live_lbl.setText("—")
                self._live_on = False
        else:
            # Vypnúť live: skryť label, ukázať DrawView
            self._live_timer.stop()
            try:
                self._lp.stop()
            except Exception:
                pass
            self._live_on = False
            self.btn_live.setText("Live OFF")
            self.live_lbl.hide()
            self.view.show()
            self.shape_sel.setEnabled(True)
            self.type_sel.setEnabled(True)

    def _live_tick(self):
        img = self._lp.last_frame_u8()
        if img is None:
            return
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy()).scaled(self.live_lbl.width(), self.live_lbl.height(),
                                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.live_lbl.setPixmap(pm)

    # ---------- UI util ----------
    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H, W)
        h, w = img_u8.shape[:2]
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())
        self.view.set_background(pm)

    def _current_golden_image(self) -> Optional[np.ndarray]:
        if self.current_img is not None:
            return self.current_img

        golden = getattr(self.recipes.tool, "golden", None)
        if isinstance(golden, np.ndarray):
            return golden

        recipe = self._current_recipe_name()
        path = Path("/data") / "recipes" / recipe / "golden.png"
        if not path.exists():
            return None

        try:
            import imageio.v3 as iio

            img = iio.imread(path)
        except Exception:
            return None

        if img.ndim == 3:
            img = img[:, :, 0]
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        return img

    # ---------- Akcie ----------
    def _capture_golden(self):
        try:
            # ak je live ON, zober aktuálny frame a hneď live vypni (freeze)
            frame = (self._lp.last_frame_u8() if self._live_on else None)
            if frame is None:
                frame = self.cam.one_shot()
            self.current_img = frame
            self._set_pixmap(frame)
            if self._live_on:
                self.btn_live.setChecked(False)
                self._toggle_live(False)  # vypnúť live, prepnúť späť na DrawView

            # po vypnutí live obnov kameru
            try:
                self.cam.resume_after_external()
            except Exception as e:
                print("[GoldenWizard] resume_after_external:", e)

            self._info("Golden zachytený z kamery.")
        except Exception as e:
            self._err(f"Zachytenie zlyhalo: {e}")

    def _load_golden(self):
        from PySide6.QtWidgets import QFileDialog
        fp, _ = QFileDialog.getOpenFileName(self, "Načítaj obrázok", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not fp:
            return
        import imageio.v3 as iio, numpy as np, cv2
        img = iio.imread(fp)
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.shape[2]==3 else img[:,:,0]
        if img.dtype != np.uint8:
            img = cv2.convertScaleAbs(img)
        self.current_img = img
        self._set_pixmap(img)
        self._info("Golden načítaný z disku.")

    def _save_recipe(self):
        if self.current_img is None:
            self._err("Najprv zachyť alebo načítaj GOLDEN.")
            return
        regs = self.view.export_regions()
        pose_enabled = self.chk_pose.isChecked()
        ok, msg = validate_cardinality([Region(**r) for r in regs], pose_required=pose_enabled)
        if not ok:
            self._err(msg); return

        name = self.recipe_name.text().strip() or "default"
        # ulož golden
        golden_path = save_golden(self.current_img, name)
        # ulož regions.json
        recipe_dir = Path("/data") / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        recipe_data = RecipeData(pose_enabled=pose_enabled, regions=regs)
        self.recipes.save_regions(name, recipe_data)

        if not self._persist_tools(name):
            return

        self._info(f"Recept uložený:\n{golden_path}\n{recipe_dir/'regions.json'}")

    def _save_validation(self, is_ok: bool):
        if self.current_img is None:
            try:
                self.current_img = (self._lp.last_frame_u8() if self._live_on else None) or self.cam.one_shot()
            except Exception as e:
                self._err(f"Zachytenie zlyhalo: {e}")
                return
        name = self.recipe_name.text().strip() or "default"
        out = save_validation_image(self.current_img, ok=is_ok, recipe_name=name)
        self._info(f"Validačný snímok uložený:\n{out['thumb']}\n{out['full']}")

    # ---------- Info/Err ----------
    def _info(self, msg):
        QMessageBox.information(self, "Info", msg)

    def _err(self, msg):
        QMessageBox.critical(self, "Chyba", msg)

    # ---------- Tools management ----------
    def _current_recipe_name(self) -> str:
        return self.recipe_name.text().strip() or "default"

    def _on_recipe_changed(self):
        recipe = self._current_recipe_name()
        if recipe == getattr(self, "_last_recipe", None):
            return
        try:
            self.recipes.load_tools(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {recipe}: {exc}")
        self._last_recipe = recipe
        self._refresh_tools_table()

    def _open_tool_catalog(self):
        dialog = ToolCatalogDialog(self.recipes.tool, self)
        if dialog.exec() != QDialog.Accepted:
            return
        tool_type = dialog.selected_type()
        if not tool_type:
            return
        try:
            tool = self.recipes.tool.make_default_tool(tool_type)
            self.recipes.add_tool(self._current_recipe_name(), tool)
            self._refresh_tools_table()
        except Exception as exc:
            self._err(f"Pridanie nástroja zlyhalo: {exc}")

    def _refresh_tools_table(self):
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        self.tools_table.setRowCount(len(tools))
        for row, tool in enumerate(tools):
            order_item = QTableWidgetItem(str(tool.order + 1))
            name_item = QTableWidgetItem(tool.name)
            type_item = QTableWidgetItem(tool.type)
            enabled_item = QTableWidgetItem("Yes" if tool.enabled else "No")
            enabled_item.setTextAlignment(Qt.AlignCenter)
            order_item.setTextAlignment(Qt.AlignCenter)
            self.tools_table.setItem(row, 0, order_item)
            self.tools_table.setItem(row, 1, name_item)
            self.tools_table.setItem(row, 2, type_item)
            self.tools_table.setItem(row, 3, enabled_item)

            actions_widget = QWidget(self.tools_table)
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(4)

            btn_up = QPushButton("Up", actions_widget)
            btn_up.clicked.connect(lambda _, idx=row: self._move_tool(idx, -1))
            btn_down = QPushButton("Down", actions_widget)
            btn_down.clicked.connect(lambda _, idx=row: self._move_tool(idx, 1))
            btn_edit = QPushButton("Edit", actions_widget)
            btn_edit.clicked.connect(lambda _, idx=row: self._edit_tool(idx))
            btn_del = QPushButton("Delete", actions_widget)
            btn_del.clicked.connect(lambda _, idx=row: self._delete_tool(idx))

            actions_layout.addWidget(btn_up)
            actions_layout.addWidget(btn_down)
            actions_layout.addWidget(btn_edit)
            actions_layout.addWidget(btn_del)
            actions_layout.addStretch(1)

            self.tools_table.setCellWidget(row, 4, actions_widget)

        self.tools_table.resizeRowsToContents()

    def _move_tool(self, index: int, delta: int):
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        target = index + delta
        if target < 0 or target >= len(tools):
            return
        order = list(range(len(tools)))
        order[index], order[target] = order[target], order[index]
        try:
            self.recipes.reorder_tools(recipe, order)
        except Exception as exc:
            self._err(f"Zmena poradia zlyhala: {exc}")
            return
        self._refresh_tools_table()

    def _delete_tool(self, index: int):
        recipe = self._current_recipe_name()
        self.recipes.remove_tool(recipe, index)
        self._refresh_tools_table()

    def _edit_tool(self, index: int):
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        if 0 <= index < len(tools):
            tool = tools[index]
            try:
                meta = self.recipes.tool.get_tool_meta(tool.type)
            except KeyError:
                self._err(f"Neznámy typ nástroja: {tool.type}")
                return

            golden_img = self._current_golden_image()
            dialog = ToolEditDialog(tool, golden_img, meta, self)
            if dialog.exec() != QDialog.Accepted:
                return

            updated_tool = dialog.result_tool()
            try:
                self.recipes.update_tool(recipe, index, updated_tool)
            except Exception as exc:
                self._err(f"Uloženie nástroja zlyhalo: {exc}")
                return
            self._refresh_tools_table()

    def _persist_tools(self, recipe: str) -> bool:
        tools = self.recipes.get_draft_tools(recipe)
        try:
            self.recipes.save_tools(recipe, tools)
        except Exception as exc:
            self._err(f"Ukladanie nástrojov zlyhalo: {exc}")
            return False
        return True

    # ---------- Shutdown ----------
    def closeEvent(self, e):
        try:
            self._live_timer.stop()
            self._lp.stop()
        except Exception:
            pass
        e.accept()

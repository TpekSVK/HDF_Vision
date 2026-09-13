"""Golden image loading and canvas presentation."""
from app.ui.filtered_roi import compose_filtered_roi, golden_filtered_roi
from PySide6.QtGui import QPixmap, QImage
from pathlib import Path
from typing import Optional, Sequence
import numpy as np
from app.models.schema import Tool
from app.services.view_images import view_uses_global_golden


class GoldenPreview:
    def __init__(self, dialog):
        self.dialog = dialog

    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H, W)
        h, w = img_u8.shape[:2]
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())
        self.dialog._canvas_empty.hide()
        self.dialog.roi_editor.show()
        self.dialog.view.set_background(pm)
        self.dialog.roi_editor.set_background(pm)
        print("[FIT_TO_VIEW] golden wizard initial image fit scheduled")
        self.dialog.view.schedule_fit_to_view(source="golden_wizard_set_pixmap")
        self.dialog.roi_editor.schedule_fit_to_view(source="golden_wizard_workspace_set_pixmap")


    def _load_saved_golden_image(
        self,
        recipe: Optional[str] = None,
        view_id: Optional[str] = None,
    ) -> Optional[np.ndarray]:
        recipe = recipe or self.dialog._current_recipe_name()
        view = self.dialog._view_by_id(view_id or self.dialog._active_view_id)
        golden_name = view.golden_path if view else "golden.png"
        path = self.dialog.recipes.base / "recipes" / recipe / golden_name
        if not path.exists():
            return None

        try:
            import imageio.v3 as iio

            image = iio.imread(path)
        except Exception:
            return None

        if image.ndim == 3:
            image = image[:, :, 0]
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        return image


    def _refresh_golden_background(
        self,
        recipe: Optional[str] = None,
        view_id: Optional[str] = None,
    ) -> None:
        recipe = recipe or self.dialog._current_recipe_name()
        view_id = view_id or self.dialog._active_view_id
        if not view_id:
            self.dialog.current_img = None
            self.dialog.view.set_background(None)
            self.dialog.view.set_tool_overlay(None)
            self.dialog.roi_editor.set_background(None)
            self.dialog.view.hide()
            self.dialog.roi_editor.hide()
            self.dialog._canvas_empty.show()
            return

        view = self.dialog._view_by_id(view_id)
        state = self.dialog._view_states.setdefault(view_id, {})
        cached = state.get("golden_image")
        if cached is not None:
            self.dialog.current_img = np.asarray(cached).copy()
            self.dialog._set_pixmap(self.dialog.current_img)
            self.dialog._set_selected_tool_overlay()
            return

        saved_golden: Optional[np.ndarray] = None
        if view_uses_global_golden(view) and recipe == getattr(
            self.dialog.recipes.tool, "recipe", None
        ):
            cached_tool = getattr(self.dialog.recipes.tool, "golden", None)
            if isinstance(cached_tool, np.ndarray):
                saved_golden = np.asarray(cached_tool)
        if saved_golden is None:
            saved_golden = self.dialog._load_saved_golden_image(recipe, view_id)
        if saved_golden is None:
            self.dialog.current_img = None
            self.dialog.view.set_background(None)
            self.dialog.view.set_tool_overlay(None)
            self.dialog.roi_editor.set_background(None)
            self.dialog.view.hide()
            self.dialog.roi_editor.hide()
            self.dialog._canvas_empty.show()
            state["golden_image"] = None
            return

        saved_array = np.asarray(saved_golden)
        self.dialog.current_img = saved_array.copy()
        state["golden_image"] = self.dialog.current_img.copy()
        self.dialog._set_pixmap(self.dialog.current_img)
        self.dialog._set_selected_tool_overlay()


    def _set_selected_tool_overlay(self, tools: Optional[Sequence[Tool]] = None) -> None:
        self.dialog._filtered_roi_timer.start()
        if tools is None:
            recipe = self.dialog._current_recipe_name()
            view_id = self.dialog._active_view_id
            if not view_id:
                tools = []
            else:
                tools = self.dialog.recipes.get_draft_tools(recipe, view_id)

        row = getattr(self.dialog, "_selected_tool_row", -1)
        if tools is not None and 0 <= row < len(tools):
            self.dialog.view.set_tool_overlay(tools[row])
            self.dialog._syncing_workspace_roi = True
            try:
                self.dialog._configure_workspace_editor(tools[row])
            finally:
                self.dialog._syncing_workspace_roi = False
        else:
            self.dialog.view.set_tool_overlay(None)
            self.dialog.roi_editor.set_result_overlay(None)
            self.dialog._syncing_workspace_roi = True
            try:
                self.dialog.roi_editor.set_locator_mode(False)
                self.dialog.roi_editor.set_roi_data({})
                self.dialog.roi_editor.configure_ignore_mask(False)
                self.dialog.roi_editor.configure_edge_anchors(False)
            finally:
                self.dialog._syncing_workspace_roi = False


    def _refresh_filtered_roi(self):
        image = self.dialog._current_golden_image()
        if image is None:
            self.dialog.lbl_filtered_roi.setText("Najprv načítajte golden snímku.")
            return
        displayed = image
        label = ""
        if self.dialog.chk_filtered_roi.isChecked():
            tools = self.dialog.recipes.get_draft_tools(self.dialog._current_recipe_name(), self.dialog._active_view_id) if self.dialog._active_view_id else []
            row = getattr(self.dialog, "_selected_tool_row", -1)
            label = "Vyberte nástroj."
            if 0 <= row < len(tools):
                try:
                    preview = golden_filtered_roi(image, tools[row])
                    if preview is not None:
                        displayed = compose_filtered_roi(image, preview)
                        label = preview["label"] + " · GOLDEN"
                    else:
                        label = "Tento nástroj zatiaľ neposkytuje filtrovaný náhľad."
                except Exception as exc:
                    label = "Náhľad nie je dostupný: " + str(exc)
        self.dialog.lbl_filtered_roi.setText(label)
        # Update only display pixels; current_img and saved golden stay original.
        displayed = np.ascontiguousarray(displayed)
        h, w = displayed.shape[:2]
        qimg = QImage(displayed.data, w, h, displayed.strides[0], QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())
        self.dialog.view.update_display_pixmap(pm)
        self.dialog.roi_editor.update_display_pixmap(pm)


    def _current_golden_image(self) -> Optional[np.ndarray]:
        if self.dialog.current_img is not None:
            return self.dialog.current_img

        view_id = self.dialog._active_view_id
        if view_id:
            state = self.dialog._view_states.get(view_id, {})
            cached = state.get("golden_image")
            if cached is not None:
                return np.asarray(cached).copy()

        view = self.dialog._view_by_id(view_id)
        if view_uses_global_golden(view):
            golden = getattr(self.dialog.recipes.tool, "golden", None)
            if isinstance(golden, np.ndarray):
                return np.asarray(golden).copy()

        return self.dialog._load_saved_golden_image(view_id=view_id)



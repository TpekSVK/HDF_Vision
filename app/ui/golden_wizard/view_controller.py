"""View selection, editing and draft change tracking for the wizard."""
from PySide6.QtWidgets import QDialog
from typing import Any, Optional, Sequence
import numpy as np
from app.models.schema import RecipeView
from app.ui.golden_wizard.view_config_dialog import ViewConfigDialog


class GoldenViews:
    def __init__(self, dialog):
        self.dialog = dialog

    def _view_by_id(self, view_id: Optional[str]) -> Optional[RecipeView]:
        if not view_id:
            return None
        for view in self.dialog._views:
            if view.id == view_id:
                return view
        return None


    def _store_view_state(self, view_id: Optional[str] = None) -> None:
        view_id = view_id or self.dialog._active_view_id
        if not view_id:
            return
        if view_id not in {view.id for view in self.dialog._views}:
            return
        state = self.dialog._view_states.setdefault(view_id, {})
        state["golden_image"] = None if self.dialog.current_img is None else np.asarray(self.dialog.current_img).copy()


    def _refresh_view_list(
        self,
        *,
        recipe: Optional[str] = None,
        select_view_id: Optional[str] = None,
        reset_states: bool = False,
    ) -> None:
        self.dialog._store_view_state()
        recipe = recipe or self.dialog._current_recipe_name()
        if reset_states:
            self.dialog._view_states = {}
        try:
            views = self.dialog.recipes.list_views(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] list_views failed for {recipe}: {exc}")
            views = []
        if not views:
            views = [RecipeView(id="view_1", name="View 1", golden_path="golden.png", tools=[])]

        self.dialog._views = [view.copy() for view in views]
        valid_ids = {view.id for view in self.dialog._views}

        if reset_states:
            self.dialog._saved_snapshots[recipe] = {}
            self.dialog._dirty_views[recipe] = {}
        else:
            self.dialog._saved_snapshots.setdefault(recipe, {})
            self.dialog._dirty_views.setdefault(recipe, {})
            for stale in list(self.dialog._saved_snapshots[recipe].keys()):
                if stale not in valid_ids:
                    self.dialog._saved_snapshots[recipe].pop(stale, None)
            for stale in list(self.dialog._dirty_views[recipe].keys()):
                if stale not in valid_ids:
                    self.dialog._dirty_views[recipe].pop(stale, None)
            for stale in list(self.dialog._view_states.keys()):
                if stale not in valid_ids:
                    self.dialog._view_states.pop(stale, None)

        for view in self.dialog._views:
            self.dialog._view_states.setdefault(view.id, {})

        self.dialog._refresh_view_metadata()

        target_view_id = select_view_id or self.dialog._active_view_id
        if not target_view_id or target_view_id not in valid_ids:
            target_view_id = self.dialog._views[0].id

        index = self.dialog._view_selector.findData(target_view_id)
        if index >= 0:
            self.dialog._updating_view_selector = True
            self.dialog._view_selector.setCurrentIndex(index)
            self.dialog._updating_view_selector = False

        recipe_snapshots = self.dialog._saved_snapshots.setdefault(recipe, {})
        recipe_dirty = self.dialog._dirty_views.setdefault(recipe, {})

        for view in self.dialog._views:
            try:
                self.dialog.recipes.load_tools(recipe, use_draft=True, view_id=view.id)
            except Exception as exc:
                print(f"[GoldenWizard] load_tools failed for {recipe}/{view.id}: {exc}")
            if reset_states or view.id not in recipe_snapshots:
                self.dialog._record_saved_snapshot(recipe, view.id)
            else:
                # Preserve existing dirty flag while ensuring entry exists.
                recipe_dirty.setdefault(view.id, recipe_dirty.get(view.id, False))

        self.dialog._switch_active_view(target_view_id, refresh_selector=False)
        self.dialog._update_window_title_dirty()


    def _on_view_changed(self) -> None:
        if self.dialog._updating_view_selector:
            return
        view_id = self.dialog._view_selector.currentData()
        if not isinstance(view_id, str) or not view_id:
            return
        if view_id == self.dialog._active_view_id:
            return
        self.dialog._switch_active_view(view_id, refresh_selector=False)


    def _switch_active_view(self, view_id: Optional[str], *, refresh_selector: bool = True) -> None:
        if not view_id:
            return
        if refresh_selector:
            index = self.dialog._view_selector.findData(view_id)
            if index >= 0:
                self.dialog._updating_view_selector = True
                self.dialog._view_selector.setCurrentIndex(index)
                self.dialog._updating_view_selector = False

        if view_id != self.dialog._active_view_id:
            self.dialog._store_view_state(self.dialog._active_view_id)
            self.dialog._active_view_id = view_id

        view = self.dialog._view_by_id(view_id)
        self.dialog._apply_view_camera_profile(view)

        recipe = self.dialog._current_recipe_name()
        try:
            self.dialog.recipes.load_tools(recipe, use_draft=True, view_id=view_id)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {recipe}/{view_id}: {exc}")

        self.dialog._selected_tool_row = -1
        self.dialog.tools_table.clearSelection()
        self.dialog._tool_panel.clear()
        self.dialog._refresh_tools_table()
        self.dialog._refresh_golden_background(recipe, view_id=view_id)
        self.dialog._on_tool_selection_changed()
        self.dialog._update_dirty_state(recipe, view_id)


    @staticmethod
    def _suggest_view_id(existing: Sequence[RecipeView]) -> str:
        existing_ids = {view.id for view in existing if getattr(view, "id", "")}
        index = 1
        while True:
            candidate = f"view_{index}"
            if candidate not in existing_ids:
                return candidate
            index += 1


    @staticmethod
    def _suggest_view_name(existing: Sequence[RecipeView]) -> str:
        existing_names = {view.name for view in existing if getattr(view, "name", "")}
        index = 1
        candidate = f"View {index}"
        while candidate in existing_names:
            index += 1
            candidate = f"View {index}"
        return candidate


    def _on_add_view(self) -> None:
        recipe = self.dialog._current_recipe_name()
        try:
            existing = self.dialog.recipes.list_views(recipe)
        except Exception as exc:
            self.dialog._err(f"Načítanie view zlyhalo: {exc}")
            return

        proposed_id = self.dialog._suggest_view_id(existing)
        proposed_name = self.dialog._suggest_view_name(existing)
        source_view = self.dialog._view_by_id(self.dialog._active_view_id)

        dialog = ViewConfigDialog(
            parent=self.dialog,
            mode="add",
            view_id=proposed_id,
            name=proposed_name,
            available_resolutions=self.dialog._available_camera_resolutions(),
            current_camera=self.dialog._current_camera_config(),
            capture_mode=self.dialog._runtime_capture_mode(),
            camera_profile=source_view.camera_profile if source_view else None,
            camera_model=self.dialog._camera_model(),
            supported_v4l2_controls=self.dialog._camera_v4l2_controls(),
            settle_ms=source_view.settle_ms if source_view else None,
            flash_delay_ms=int(getattr(source_view, "flash_delay_ms", 0) or 0)
            if source_view
            else 0,
            flash_pulse_ms=int(getattr(source_view, "flash_pulse_ms", 200) or 200)
            if source_view
            else 200,
            pico_profile=getattr(source_view, "pico_profile", None) if source_view else None,
            pico_config_snapshot=self.dialog._read_pico_config_snapshot(),
            trigger_mode=getattr(source_view, "trigger_mode", "timed") if source_view else "timed",
            external_trigger_mode=getattr(source_view, "external_trigger_mode", None)
            if source_view
            else None,
            external_source=getattr(source_view, "external_source", None) if source_view else None,
            external_request_input=getattr(source_view, "external_request_input", None)
            if source_view
            else None,
            trigger_interval_ms=getattr(source_view, "trigger_interval_ms", None)
            if source_view
            else None,
            trigger_gap_ms=getattr(source_view, "trigger_gap_ms", None)
            if source_view
            else None,
            available_frame_sources=[
                (view.id, view.name or view.id)
                for view in existing
                if view.id
            ],
            frame_source_view_id=getattr(source_view, "frame_source_view_id", None)
            if source_view
            else None,
            image_rotation=getattr(source_view, "image_rotation", 0) if source_view else 0,
            available_branch_targets=[
                (view.id, view.name or view.id)
                for view in existing
                if view.id
            ],
            branch_enabled=bool(getattr(source_view, "branch_enabled", False))
            if source_view
            else False,
            branch_targets=dict(getattr(source_view, "branch_targets", {}) or {})
            if source_view
            else None,
            branch_default_view_id=(
                getattr(source_view, "branch_default_view_id", None)
                if source_view
                else None
            ),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        if not self.dialog._authorize_write():
            return

        data = dialog.values()
        try:
            new_view = self.dialog.recipes.add_view(
                recipe,
                source_view_id=self.dialog._active_view_id,
                view_id=dialog.view_id(),
                view_name=data.get("name"),
                frame_source_view_id=data.get("frame_source_view_id"),
                camera_profile=data.get("camera_profile"),
                settle_ms=data.get("settle_ms"),
                flash_delay_ms=data.get("flash_delay_ms"),
                flash_pulse_ms=data.get("flash_pulse_ms"),
                pico_profile=data.get("pico_profile"),
                trigger_mode=data.get("trigger_mode"),
                external_trigger_mode=data.get("external_trigger_mode"),
                external_source=data.get("external_source"),
                external_request_input=data.get("external_request_input"),
                trigger_interval_ms=data.get("trigger_interval_ms"),
                trigger_gap_ms=data.get("trigger_gap_ms"),
                image_rotation=int(data.get("image_rotation", 0) or 0),
                branch_enabled=bool(data.get("branch_enabled", False)),
                branch_targets=dict(data.get("branch_targets", {}) or {}),
                branch_default_view_id=data.get("branch_default_view_id"),
            )
        except Exception as exc:
            self.dialog._err(f"Pridanie view zlyhalo: {exc}")
            return

        self.dialog._view_states.setdefault(new_view.id, {})
        self.dialog._refresh_view_list(
            recipe=recipe, select_view_id=new_view.id, reset_states=False
        )
        self.dialog._refresh_publish_state()


    def _on_edit_view(self) -> None:
        recipe = self.dialog._current_recipe_name()
        view = self.dialog._view_by_id(self.dialog._active_view_id)
        if not view:
            return

        dialog = ViewConfigDialog(
            parent=self.dialog,
            mode="edit",
            view_id=view.id,
            name=view.name or view.id,
            available_resolutions=self.dialog._available_camera_resolutions(),
            current_camera=self.dialog._current_camera_config(),
            capture_mode=self.dialog._runtime_capture_mode(),
            camera_profile=view.camera_profile,
            camera_model=self.dialog._camera_model(),
            supported_v4l2_controls=self.dialog._camera_v4l2_controls(),
            settle_ms=view.settle_ms,
            flash_delay_ms=int(getattr(view, "flash_delay_ms", 0) or 0),
            flash_pulse_ms=int(getattr(view, "flash_pulse_ms", 200) or 200),
            pico_profile=getattr(view, "pico_profile", None),
            pico_config_snapshot=self.dialog._read_pico_config_snapshot(),
            trigger_mode=getattr(view, "trigger_mode", "timed"),
            external_trigger_mode=getattr(view, "external_trigger_mode", None),
            external_source=getattr(view, "external_source", None),
            external_request_input=getattr(view, "external_request_input", None),
            trigger_interval_ms=getattr(view, "trigger_interval_ms", None),
            trigger_gap_ms=getattr(view, "trigger_gap_ms", None),
            available_frame_sources=[
                (other.id, other.name or other.id)
                for other in self.dialog._views
                if other.id and other.id != view.id
            ],
            frame_source_view_id=getattr(view, "frame_source_view_id", None),
            image_rotation=getattr(view, "image_rotation", 0),
            available_branch_targets=[
                (other.id, other.name or other.id)
                for other in self.dialog._views
                if other.id and other.id != view.id
            ],
            branch_enabled=bool(getattr(view, "branch_enabled", False)),
            branch_targets=dict(getattr(view, "branch_targets", {}) or {}),
            branch_default_view_id=getattr(view, "branch_default_view_id", None),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        if not self.dialog._authorize_write():
            return

        data = dialog.values()
        try:
            updated_view = self.dialog.recipes.update_view(
                recipe,
                view.id,
                view_name=data.get("name"),
                frame_source_view_id=data.get("frame_source_view_id"),
                camera_profile=data.get("camera_profile"),
                settle_ms=data.get("settle_ms"),
                flash_delay_ms=data.get("flash_delay_ms"),
                flash_pulse_ms=data.get("flash_pulse_ms"),
                pico_profile=data.get("pico_profile"),
                trigger_mode=data.get("trigger_mode"),
                external_trigger_mode=data.get("external_trigger_mode"),
                external_source=data.get("external_source"),
                external_request_input=data.get("external_request_input"),
                trigger_interval_ms=data.get("trigger_interval_ms"),
                trigger_gap_ms=data.get("trigger_gap_ms"),
                image_rotation=int(data.get("image_rotation", 0) or 0),
                branch_enabled=bool(data.get("branch_enabled", False)),
                branch_targets=dict(data.get("branch_targets", {}) or {}),
                branch_default_view_id=data.get("branch_default_view_id"),
            )
        except Exception as exc:
            self.dialog._err(f"Úprava view zlyhala: {exc}")
            return

        self.dialog._refresh_view_list(
            recipe=recipe, select_view_id=updated_view.id, reset_states=False
        )
        self.dialog._refresh_publish_state()


    def _on_remove_view(self) -> None:
        recipe = self.dialog._current_recipe_name()
        view_id = self.dialog._active_view_id
        if not view_id:
            return
        if not self.dialog._authorize_write():
            return
        try:
            remaining = self.dialog.recipes.remove_view(recipe, view_id)
        except ValueError as exc:
            self.dialog._warn(str(exc))
            return
        except Exception as exc:
            self.dialog._err(f"Odstránenie view zlyhalo: {exc}")
            return
        self.dialog._view_states.pop(view_id, None)
        next_view_id = remaining[0].id if remaining else None
        self.dialog._refresh_view_list(recipe=recipe, select_view_id=next_view_id, reset_states=False)
        self.dialog._refresh_publish_state()


    def _refresh_view_metadata(self) -> None:
        self.dialog._updating_view_selector = True
        self.dialog._view_selector.blockSignals(True)
        self.dialog._view_selector.clear()
        for view in self.dialog._views:
            label = view.name or view.id or "View"
            self.dialog._view_selector.addItem(label, view.id)
        self.dialog._view_selector.blockSignals(False)
        self.dialog._updating_view_selector = False

        self.dialog.btn_remove_view.setEnabled(len(self.dialog._views) > 1)
        self.dialog.btn_edit_view.setEnabled(bool(self.dialog._views))


    def _snapshot_tools(self, recipe: str, view_id: str) -> list[dict[str, Any]]:
        try:
            tools = self.dialog.recipes.get_draft_tools(recipe, view_id)
        except Exception:
            return []
        return [tool.to_dict() for tool in tools]


    def _record_saved_snapshot(self, recipe: str, view_id: Optional[str] = None) -> None:
        view_id = view_id or self.dialog._active_view_id
        if not view_id:
            return
        snapshot = self.dialog._snapshot_tools(recipe, view_id)
        recipe_snapshots = self.dialog._saved_snapshots.setdefault(recipe, {})
        recipe_snapshots[view_id] = snapshot
        self.dialog._dirty_views.setdefault(recipe, {})[view_id] = False


    def _update_dirty_state(self, recipe: Optional[str] = None, view_id: Optional[str] = None) -> None:
        if getattr(self.dialog, "_filtered_roi_timer", None) is not None and self.dialog.chk_filtered_roi.isChecked():
            self.dialog._filtered_roi_timer.start()
        if not hasattr(self.dialog, "_saved_snapshots"):
            return
        recipe = recipe or self.dialog._current_recipe_name()
        if view_id is None:
            if self.dialog._active_view_id:
                self.dialog._update_dirty_state(recipe, self.dialog._active_view_id)
            return
        current = self.dialog._snapshot_tools(recipe, view_id)
        saved = self.dialog._saved_snapshots.get(recipe, {}).get(view_id)
        dirty = saved is None or current != saved
        self.dialog._dirty_views.setdefault(recipe, {})[view_id] = dirty
        self.dialog._update_window_title_dirty()


    def _has_unsaved_changes(self) -> bool:
        if not hasattr(self.dialog, "_dirty_views"):
            return False
        for view_map in self.dialog._dirty_views.values():
            if any(view_map.values()):
                return True
        return False



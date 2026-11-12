from types import SimpleNamespace
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from app.services.camera_registry import CameraRegistry, CameraRouter, PicoRouter
from app.services.recipe_service import RecipeService
from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.golden_wizard.view_config_dialog import ViewConfigDialog


def test_create_publish_and_reopen_two_camera_recipe_through_real_view_dialogs(tmp_path, monkeypatch):
    app=QApplication.instance() or QApplication([])
    recipes=RecipeService(tmp_path); recipes.create('Dve kamery'); recipes.load('Dve kamery')
    def camera():
        cam=SimpleNamespace(width=1280,height=720,fps=60,pixel_format='Y8',exposure_us=1000,
                            gain_db=0,get_supported_v4l2_controls=lambda:{'exposure_time_absolute'},
                            get_camera_model=lambda:'See3CAM CU55')
        cam.apply_resolution=lambda **kw: None
        cam.set_manual_exposure_us=lambda value: setattr(cam,'exposure_us',value)
        return cam
    def pico():
        return SimpleNamespace(prepare_master=lambda camera: None, prepare_trigger=lambda camera: None,
            set_manual_light=lambda enabled: True, manual_light_status=lambda:False,last_error='')
    registry=CameraRegistry({'camera_1':camera(),'camera_2':camera()},
        {'camera_1':pico(),'camera_2':pico()},serials={'camera_1':'USB-A','camera_2':'USB-B'})
    def capture(**kw):
        view=recipes.get_view('Dve kamery',kw['view_id'])
        registry.select(view.camera_id)
        return np.full((720,1280),80 if view.camera_id=='camera_1' else 160,np.uint8)
    wizard=GoldenWizard(CameraRouter(registry),recipes,pico=PicoRouter(registry),
        get_capture_mode=lambda:'master',capture_frame_for_golden=capture)
    wizard._info=lambda message:None
    wizard._err=lambda message: (_ for _ in ()).throw(AssertionError(message))
    wizard._warn=wizard._err
    target=['camera_1']
    def edit(dialog):
        dialog._camera_combo.setCurrentIndex(dialog._camera_combo.findData(target[0]))
        dialog._output_group_combo.setCurrentIndex(dialog._output_group_combo.findData('group_1'))
        dialog._output_mode_combo.setCurrentIndex(dialog._output_mode_combo.findData('AND'))
        dialog._and_timeout.setValue(2000)
        dialog._interval_edit.setText('1000')
        dialog.accept()
        assert dialog.values()['camera_id']==target[0]
        return QDialog.Accepted
    monkeypatch.setattr(ViewConfigDialog,'exec',edit)
    try:
        wizard._view_controller._on_edit_view()
        wizard._capture_golden()
        assert wizard._persist_recipe_assets()[0]
        target[0]='camera_2'
        wizard._view_controller._on_add_view()
        assert wizard._active_view_id=='view_2' and registry.selected=='camera_2'
        wizard._capture_golden()
        assert wizard._persist_recipe_assets()[0]
        learning_frame = wizard._capture_presence_learning_frame('view_2')
        assert learning_frame.shape == (720, 1280) and np.all(learning_frame == 160)
        wizard._switch_active_view('view_1')
        assert np.all(wizard._capture_presence_learning_frame('view_1') == 80)
        wizard._switch_active_view('view_2')
        recipes.publish_recipe('Dve kamery')
        published=recipes._load_published_recipe_config('Dve kamery')
        assert [view.camera_id for view in published.views]==['camera_1','camera_2']
        assert all(view.output_group=='group_1' and view.output_mode=='AND' and view.and_timeout_ms==2000
                   for view in published.views)
        assert published.views[1].golden_camera=={'camera_id':'camera_2','camera_serial':'USB-B'}
        assert all((tmp_path/'recipes'/'Dve kamery'/view.golden_path).exists() for view in published.views)
        wizard._switch_active_view('view_1')
        registry.cameras.pop('camera_2')
        wizard._switch_active_view('view_2')
        assert wizard._camera_capture_error and not wizard.btn_live.isEnabled()
        assert not wizard.btn_capture_golden.isEnabled()
    finally:
        wizard._has_unsaved_changes=lambda:False; wizard._dirty_views={}; wizard.reject()
        wizard.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        recipes.db.close()

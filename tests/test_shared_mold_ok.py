from types import SimpleNamespace, MethodType
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from app.models.schema import ToolRoi, RecipeV2, ToolMask
from app.services.learning_context import shared_capture_signature, learning_signature, bind_samples
from app.services.shared_mold_ok import SharedMoldOK
from app.services.statistical_learning import LearningSamplePreparer
from app.services.tool_registry import ToolRegistry
from app.services.presence_absence_v2_service import load_samples, save_sample, reset_learning_assets
from app.services.learning_dataset import LearningDataset
from app.models.learning_contract import SAMPLE_PREPARATION_VERSION


def scene():
    golden=np.random.default_rng(42).integers(0,255,(60,80),dtype=np.uint8)
    a=ToolRegistry.make_default_tool('mold.protection_v1')
    b=a.copy();b.name='Second'
    a.roi=ToolRoi(dict(x=5,y=8,w=12,h=10))
    b.roi=ToolRoi(dict(shape='polygon',points=[[30,20],[50,20],[40,40]]))
    return golden,RecipeV2().views[0],a,b


def test_shared_frames_prepare_separate_roi_models_and_keep_nok(tmp_path):
    golden,view,a,b=scene();tools=[a,b]
    pool=SharedMoldOK(tmp_path,'recipe',view.id,shared_capture_signature(golden,view,tools))
    for delta in (0,1,2):pool.add(np.clip(golden.astype(int)+delta,0,255).astype(np.uint8))
    signatures=[learning_signature(golden,view,t,tools) for t in tools]
    datasets=[]
    for i,t in enumerate(tools):
        base=tmp_path/f'local-{i}';bind_samples(base,signatures[i])
        save_sample(np.ones((t.roi.rect()[3],t.roi.rect()[2]),np.uint8),base/'nok')
        count=pool.import_into(base,signatures[i],lambda f:LearningSamplePreparer().sample(golden,f,t,tools))
        assert count==3
        assert pool.import_into(base,signatures[i],lambda f:pytest.fail('Duplicate preparation'))==0
        assert len(load_samples(base/'nok'))==1
        t.params.values.update(sample_preparation_version=SAMPLE_PREPARATION_VERSION,min_ok_samples=2)
        dirs=dict(ok=base/'ok',nok=base/'nok',model=base/'model')
        LearningDataset().rebuild(t,dirs,view.id,signatures[i])
        datasets.append(load_samples(base/'ok'))
    assert datasets[0][0].shape==(10,12)
    assert datasets[1][0].shape==(20,20)
    assert (tmp_path/'local-0/model').exists() and (tmp_path/'local-1/model').exists()
    reset_learning_assets(tmp_path/'local-0')
    assert len(list(pool.frames()))==3
    assert len(load_samples(tmp_path/'local-1/ok'))==3


def test_full_color_frames_preserved_and_scope_isolated(tmp_path):
    golden,view,a,b=scene();sig=shared_capture_signature(golden,view,[a,b])
    pool=SharedMoldOK(tmp_path,'recipe',view.id,sig)
    image=np.stack([golden,np.zeros_like(golden),255-golden],axis=2)
    pool.add(image)
    np.testing.assert_array_equal(next(pool.frames())[1],image)
    for recipe,vid,signature in [('other',view.id,sig),('recipe','view_2',sig),('recipe',view.id,'other-context')]:
        assert list(SharedMoldOK(tmp_path,recipe,vid,signature).frames())==[]


@pytest.mark.parametrize('change',['roi','mask','name','threshold','camera','golden','locator'])
def test_shared_context_excludes_local_geometry_but_detects_capture_changes(change):
    golden,view,a,b=scene();tools=[a,b]
    before=shared_capture_signature(golden,view,tools)
    if change=='roi':a.roi=ToolRoi(dict(x=1,y=1,w=8,h=8))
    elif change=='mask':a.ignore_mask=ToolMask(np.ones_like(golden))
    elif change=='name':a.name='Renamed'
    elif change=='threshold':a.thresholds.values['score_threshold']=9
    elif change=='camera':view.image_rotation=90
    elif change=='golden':golden[0,0]^=255
    else:
        tools.append(ToolRegistry.make_default_tool('locator.template_match'))
    assert (shared_capture_signature(golden,view,tools)!=before)==(change in ('camera','golden','locator'))


def test_incompatible_legacy_crop_not_mixed_and_failed_preparation_is_atomic(tmp_path):
    pool=SharedMoldOK(tmp_path,'r','v','capture');pool.add(np.ones((10,10),np.uint8));pool.add(np.zeros((10,10),np.uint8))
    base=tmp_path/'local';save_sample(np.zeros((2,2),np.uint8),base/'ok')
    with pytest.raises(ValueError):pool.import_into(base,'new',lambda f:f[:2,:2])
    assert len(load_samples(base/'ok'))==1
    reset_learning_assets(base)
    calls=[]
    def prepare(f):
        calls.append(f)
        if len(calls)==2:raise ValueError('Alignment failed')
        return f[:2,:2]
    with pytest.raises(ValueError):pool.import_into(base,'new',prepare)
    assert load_samples(base/'ok')==[]


def test_capture_dialog_deletion_keeps_frames_and_crops_paired():
    from app.ui.golden_wizard.presence_v2_sample_capture_dialog import PresenceV2SampleCaptureDialog
    app=QApplication.instance() or QApplication([])
    frames=iter([np.full((10,10),v,np.uint8) for v in (1,2)])
    d=PresenceV2SampleCaptureDialog(title='test',capture_fn=lambda:next(frames),crop_fn=lambda f:f[:3,:4],retain_frames=True)
    d._capture_once();d._capture_once();d._thumbs.setCurrentRow(0);d._delete_selected()
    assert len(d.frames())==len(d.samples())==1
    assert np.all(d.frames()[0]==2) and np.all(d.samples()[0]==2)
    d.close()


def test_wizard_collection_and_reuse_do_not_change_other_tool_or_nok(tmp_path, monkeypatch):
    from app.ui.golden_wizard.golden_wizard import GoldenWizard
    import app.ui.golden_wizard.golden_wizard as module
    golden,view,a,b=scene()
    current=[a,b]
    messages=[]
    def update(recipe,row,tool,**kwargs):current[row]=tool.copy()
    recipes=SimpleNamespace(base=tmp_path,get_draft_tools=lambda *args:[t.copy() for t in current],update_tool=update)
    w=SimpleNamespace(recipes=recipes,_selected_tool_row=0,_active_view_id=view.id,
        _current_recipe_name=lambda:'recipe',_current_golden_image=lambda:golden,_view_by_id=lambda _:view,
        _authorize_write=lambda:True,_presence_tuning_results={},_warn=lambda msg:pytest.fail(msg),
        _info=messages.append,_update_dirty_state=lambda *a:None,
        _tool_panel=SimpleNamespace(refresh_values=lambda *a:None,refresh_presence_learning=lambda *a,**kw:None),
        _capture_presence_learning_frame=lambda _:golden.copy())
    for name in ('_presence_learning_signature','_shared_mold_ok_store','_prepare_presence_learning_sample','_refresh_presence_v2_learning','_apply_mold_learning'):
        setattr(w,name,MethodType(getattr(GoldenWizard,name),w))
    def context():
        row=w._selected_tool_row
        return 'recipe',view.id,row,current[row].copy(),tmp_path/f'tool-{row}'
    w._presence_v2_context=context
    # The current shared-OK flow asks whether to apply to all tools. Exercise
    # selected-only reuse, retaining the isolation assertions below.
    class Confirmation:
        Question = 0
        Yes = 1
        No = 2
        def __init__(self, *args): pass
        def __getattr__(self, name): return lambda *args: None
        def exec(self): return self.Yes
    class SelectedOnly:
        def __init__(self, *args): pass
        def setChecked(self, value): pass
        def isChecked(self): return False
    monkeypatch.setattr(module, 'QMessageBox', Confirmation)
    monkeypatch.setattr(module, 'QCheckBox', SelectedOnly)
    monkeypatch.setattr(module, 'resolve_assets_dir',
                        lambda *args: tmp_path / f'tool-{w._selected_tool_row}')
    w._refresh_tools_table = lambda: w._refresh_presence_v2_learning(
        current[w._selected_tool_row], w._selected_tool_row)
    class Capture:
        def __init__(self,**kw):
            assert kw['retain_frames'] is True
            self.full=[kw['capture_fn']()]
            self.crops=[kw['crop_fn'](self.full[0])]
            self.batch = kw['batch']
            self.validate = kw['validate_commit']
        def exec(self):
            self.validate()
            ids = [self.batch.add(crop, self.full[i] if hasattr(self, 'full') else None)
                   for i, crop in enumerate(self.crops)]
            self.saved_count = self.batch.commit(ids).result(timeout=5)
            return QDialog.Accepted
        def deleteLater(self): pass
        def samples(self):return self.crops
        def frames(self):return self.full
    monkeypatch.setattr(module,'PresenceV2SampleCaptureDialog',Capture)
    GoldenWizard._on_presence_v2_learning(w,'capture_ok')
    assert len(load_samples(tmp_path/'tool-0/ok'))==1
    snapshot=current[0].to_dict()
    w._selected_tool_row=1
    GoldenWizard._on_presence_v2_learning(w,'shared_ok')
    assert len(load_samples(tmp_path/'tool-1/ok'))==1
    assert current[1].params.values['reference_model_needs_rebuild'] is True
    assert current[1].params.values['sample_count_ok']==1
    assert current[0].to_dict()==snapshot
    GoldenWizard._on_presence_v2_learning(w,'shared_ok')
    assert len(load_samples(tmp_path/'tool-1/ok'))==1
    assert load_samples(tmp_path/'tool-1/nok')==[]
    w._selected_tool_row=0
    GoldenWizard._on_presence_v2_learning(w,'shared_ok')
    assert len(load_samples(tmp_path/'tool-0/ok'))==1
    assert current[0].to_dict()==snapshot  # Own collection is not imported twice.
    w._selected_tool_row=1
    class NOKCapture(Capture):
        def __init__(self,**kw):
            assert kw['retain_frames'] is False
            self.crops=[kw['crop_fn'](kw['capture_fn']())]
            self.batch = kw['batch']
            self.validate = kw['validate_commit']
        def frames(self):pytest.fail('NOK must not enter shared OK pool')
    monkeypatch.setattr(module,'PresenceV2SampleCaptureDialog',NOKCapture)
    GoldenWizard._on_presence_v2_learning(w,'capture_nok')
    assert len(load_samples(tmp_path/'tool-1/nok'))==1
    assert len(list(w._shared_mold_ok_store(view.id).frames()))==1
    assert current[0].to_dict()==snapshot


def test_shared_button_only_for_original_empty_mold_tool():
    from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
    app=QApplication.instance() or QApplication([])
    panel=ToolConfigPanel()
    for kind,visible in [('mold.protection_v1',True),('presence.absence_v2',False),('mold.protection_v2',False)]:
        panel.set_tool(ToolRegistry.make_default_tool(kind),ToolRegistry.get_tool_definition(kind),ToolRegistry.get_tool_schema(kind))
        assert panel._shared_mold_ok.isHidden()!=visible
    panel.close()

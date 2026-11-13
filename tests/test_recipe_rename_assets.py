import hashlib
import json
from pathlib import Path
import numpy as np
import pytest
from app.services.recipe_service import RecipeService
from app.services.tool_registry import ToolRegistry
from app.services.presence_absence_v2_service import save_model, load_model, resolve_assets_dir


def trained_recipe(tmp_path):
    service=RecipeService(tmp_path)
    service.create('old')
    config=service._load_recipe_config('old')
    config.views[0].output_group='group_1'
    service._save_recipe_config('old',config)
    service.add_view('old',view_id='view_2',view_name='diel',camera_id='camera_2',output_group='group_2')
    for view_id in ('view_1','view_2'):
        tool=ToolRegistry.make_default_tool('mold.protection_v1')
        assets=resolve_assets_dir(tmp_path,'old',view_id,'copy-stable-id')
        save_model(assets/'model',np.ones((8,8))*100,np.ones((8,8)),{'learning_signature':view_id})
        tool.params.values.update(reference_assets_dir=str(assets),reference_model_ready=True)
        service.save_tools('old',[tool],view_id=view_id)
    service.publish_recipe('old')
    return service


def test_rename_preserves_models_and_updates_both_versions_and_drafts(tmp_path):
    service=trained_recipe(tmp_path)
    root=tmp_path/'recipes/old'
    hashes={p.relative_to(root):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'tool_assets').rglob('*') if p.is_file()}
    service.tool.recipe='old'
    service.rename('old','new name')
    assert service.tool.recipe=='new name'
    for published in (False,True):
        cfg=(service._load_published_recipe_config if published else service._load_recipe_config)('new name')
        for view in cfg.views:
            tool=view.tools[0]
            assets=tool.params.values['reference_assets_dir']
            assert '/recipes/new name/' in assets
            assert load_model(Path(assets)/'model') is not None
            assert tool.params.values['reference_model_ready']
            assert service.get_draft_tools('new name',view.id)[0].params.values['reference_assets_dir']==assets
    assert 'old' not in service._draft_tools
    assert all(hashlib.sha256((tmp_path/'recipes/new name'/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    service.db.close()


def test_failed_rename_restores_original_documents_and_directory(tmp_path,monkeypatch):
    from app.services import recipe_asset_paths
    service=trained_recipe(tmp_path)
    root=tmp_path/'recipes/old'
    before={p.name:p.read_bytes() for p in root.glob('*.json')}
    write=recipe_asset_paths.write_bytes_atomically
    calls=0
    def fail_once(path,data):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('simulated write failure')
        return write(path,data)
    monkeypatch.setattr(recipe_asset_paths,'write_bytes_atomically',fail_once)
    with pytest.raises(OSError):service.rename('old','new')
    assert root.exists() and not (tmp_path/'recipes/new').exists()
    assert 'old' in service.list() and 'new' not in service.list()
    assert all((root/name).read_bytes()==data for name,data in before.items())
    service.db.close()


def test_relocation_does_not_rewrite_other_recipes_or_plain_text(tmp_path):
    from app.services.recipe_asset_paths import relocated_document
    old=tmp_path/'old';new=tmp_path/'new'
    data={'name':str(old),'params':{'reference_assets_dir':str(tmp_path/'old-other/model')}}
    assert relocated_document(json.dumps(data),old,new)==data

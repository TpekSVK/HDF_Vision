import pytest

from app.services.db_service import DbService
from app.services.recipe_audit_service import RecipeAuditService, audit_recipe_diff
from app.services.recipe_service import RecipeService


def test_schema_insert_read_filters_search_and_ascending_order(tmp_path):
    db = DbService(tmp_path / "audit.db")
    audit = RecipeAuditService(db)
    table = db.conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recipe_change_log'"
    ).fetchone()
    assert table == ("recipe_change_log",)
    audit.log_change("B", action="UPDATE", entity_type="CAMERA", view_id="v2",
                     view_name="Back", field_name="gain", old_value=1, new_value=2, ts_ms=20)
    audit.log_change("A", action="UPDATE", entity_type="CAMERA", view_id="v1",
                     view_name="Front", field_name="exposure", old_value=4000, new_value=5000, ts_ms=10)
    audit.log_change("C", action="UPDATE", entity_type="TOOL", view_id="v3",
                     view_name="Side", field_name="min_area", old_value=25, new_value=40, ts_ms=30)
    assert [row["recipe_name"] for row in audit.list_changes()] == ["A", "B", "C"]
    assert len(audit.list_changes(recipe_name="A")) == 1
    assert len(audit.list_changes(view_id="v2")) == 1
    assert len(audit.list_changes(entity_type="tool")) == 1
    assert len(audit.list_changes(search="EXPOSURE")) == 1
    assert len(audit.list_changes(search="5000")) == 1


def test_field_diff_omits_unchanged_values():
    assert audit_recipe_diff
    from app.services.recipe_audit_service import flatten_changes
    changes = flatten_changes(
        {"camera_profile": {"exposure_us": 4000, "gain": 2}, "external_request_input": "IN1"},
        {"camera_profile": {"exposure_us": 5000, "gain": 2}, "external_request_input": "IN7"},
    )
    assert changes == [
        ("camera_profile.exposure_us", 4000, 5000),
        ("external_request_input", "IN1", "IN7"),
    ]


def test_failed_save_does_not_audit(monkeypatch, tmp_path):
    service = RecipeService(base_dir=tmp_path)
    service.create("A")
    before = len(service.audit.list_changes())
    monkeypatch.setattr("app.services.recipe_service.save_recipe_config",
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        service.set_logging_enabled("A", False)
    assert len(service.audit.list_changes()) == before


def test_deleted_recipe_history_survives(tmp_path):
    service = RecipeService(base_dir=tmp_path)
    service.create("Recipe_A")
    service.set_logging_enabled("Recipe_A", False)
    service.delete("Recipe_A")
    rows = service.audit.list_changes(recipe_name="Recipe_A")
    assert [row["action"] for row in rows][0] == "CREATE"
    assert rows[-1]["action"] == "DELETE"
    assert service.db.recipe_id("Recipe_A") is None


def test_cancel_equivalent_draft_mutation_creates_no_audit(tmp_path):
    service = RecipeService(base_dir=tmp_path)
    service.create("A")
    before = len(service.audit.list_changes())
    draft = service._load_recipe_config("A")
    draft.logging_enabled = not draft.logging_enabled
    assert len(service.audit.list_changes()) == before

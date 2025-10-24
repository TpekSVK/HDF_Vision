import pytest

pytest.importorskip("cv2")

from app.models.schema import RecipeAggregation


def test_aggregation_and_mode():
    agg = RecipeAggregation(mode="AND")
    result = agg.aggregate_statuses({"view1": "ok", "view2": "nok"})
    assert result == "nok"


def test_aggregation_or_mode():
    agg = RecipeAggregation(mode="OR")
    result = agg.aggregate_statuses({"view1": "nok", "view2": "warn"})
    assert result == "warn"
    result_ok = agg.aggregate_statuses({"view1": "nok", "view2": "ok"})
    assert result_ok == "ok"


def test_aggregation_weighted_mode():
    agg = RecipeAggregation(mode="WEIGHTED", weights={"view1": 0.7, "view2": 0.3})
    result = agg.aggregate_statuses({"view1": "ok", "view2": "nok"})
    assert result == "warn"
    result2 = agg.aggregate_statuses({"view1": "nok", "view2": "ok"})
    assert result2 == "nok"


def test_aggregation_empty_statuses_returns_ok():
    agg = RecipeAggregation(mode="AND")
    assert agg.aggregate_statuses({}) == "ok"


def test_weighted_without_weights_fallbacks_to_priority():
    agg = RecipeAggregation(mode="WEIGHTED", weights={})
    result = agg.aggregate_statuses({"view1": "warn", "view2": "nok"})
    assert result == "nok"

import pytest

pytest.importorskip("cv2")

from app.models.schema import RecipeAggregation
from app.ui.branching_utils import aggregate_branching_statuses


def test_branch_router_status_is_ignored_from_aggregation():
    aggregation = RecipeAggregation(mode="AND")
    statuses = {"view_0": "nok", "view_2": "ok"}

    aggregated = aggregate_branching_statuses(
        aggregation, statuses, ignored_view_ids=["view_0"]
    )

    assert aggregated == "ok"


def test_aggregation_falls_back_to_all_statuses_when_none_ignored():
    aggregation = RecipeAggregation(mode="AND")
    statuses = {"view_0": "ok", "view_1": "warn"}

    aggregated = aggregate_branching_statuses(aggregation, statuses, ignored_view_ids=None)

    assert aggregated == "warn"

